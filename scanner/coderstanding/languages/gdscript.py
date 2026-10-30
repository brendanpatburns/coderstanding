"""GDScript reader (docs/DESIGN.md 4.3): one node per .gd file, inner classes, and refs.

Names as written go to the resolver; this module only tracks what a local name stands
for (typed / inferred vars, preload names) so calls through it reach the right type.
"""

import posixpath
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_gdscript

from .. import paths
from ..godot_files import declared_class_name
from ..language import (FIELD_MEMBER, INIT_MEMBER, Declaration, FileContext, FileResult,
                        LanguageModule, Ref, Target, record_dependency, register,
                        track_dependencies)
from ..model import (ANY_SIGNAL, EMIT_DETAIL, LAMBDA_HANDLER, SIGNAL_ARROW, VARIABLE_HANDLER, Member,
                     child_id)
from . import doc_text, ts_util as ts

PARSER = ts.make_parser(tree_sitter_gdscript.language())

# GDScript's own value types: not classes of any package, so no edges to them.
BUILTIN_TYPES = frozenset({"void", "bool", "int", "float", "String", "StringName",
                           "NodePath", "Variant", "Array", "Dictionary", "Callable",
                           "Signal"})
DOC_MARKERS = ("##",)
HEADER_TYPES = ("annotation", "extends_statement", "class_name_statement")
MEMBER_TYPES = ("variable_statement", "function_definition", "signal_statement",
                "const_statement", "class_definition", "enum_definition")
LOADERS = ("preload", "load")
CREATORS = ("new", "instantiate")
CONSTRUCTOR = "_init"  # `func _init()` is a constructor_definition node without a name
STRING_SIGNAL_CALLS = ("emit_signal", "connect")  # take the signal name as a string
OBJECT = Target(name="Object")  # what an untyped value is: signal names on it still count


def script_id(class_name, res_path):
    """Node id of a script: its class_name if it has one, else its res:// path."""
    return class_name or res_path


def string_value(node):
    return ts.text(node).lstrip("&^")[1:-1]


def is_type_like(name):
    """Class / autoload / preload names start upper case (after `_` for private inner
    classes like `_Overlay`); ALL_CAPS ones are constants."""
    return name.lstrip("_")[:1].isupper() and not (len(name) > 3 and name.isupper())


def path_target(written, res_dir):
    """Target for a path as written in a script; relative paths are relative to the script."""
    if written.startswith((paths.RES_PREFIX, "uid://")):
        return Target(path=written)
    return Target(path=paths.RES_PREFIX + posixpath.normpath(posixpath.join(res_dir, written)))


def loader_path(node):
    """The path of `preload("...")` / `load("...")`, else ""."""
    if node is None or node.type != "call":
        return ""
    callee = ts.first_child_of_type(node, "identifier")
    args = ts.first_child_of_type(node, "arguments")
    if ts.text(callee) not in LOADERS or args is None:
        return ""
    first = args.named_children[:1]
    return string_value(first[0]) if first and first[0].type == "string" else ""


def type_names(type_node):
    """Type names in a type hint, generics stripped: `Dictionary[K, Foo]` -> [K, Foo]."""
    if type_node is None:
        return []
    if type_node.type in ("identifier", "attribute"):
        name = ts.text(type_node)
        return [] if name in BUILTIN_TYPES else [name]
    return [name for child in type_node.named_children for name in type_names(child)]


def plain_type(type_node):
    """The single type a hint names for calls (`Foo`, `A.B`), None for `Array[Foo]`."""
    inner = type_node.named_children if type_node is not None else []
    if len(inner) == 1 and inner[0].type in ("identifier", "attribute"):
        names = type_names(inner[0])
        return Target(name=names[0]) if names else None
    return None


def creation_target(value, res_dir):
    """Target of `Foo.new()` / `A.B.new()` / `preload(p).new()`, else None."""
    if value is None or value.type != "attribute":
        return None
    parts = value.named_children
    last = parts[-1]
    if member_name(last) != "new" or last.type != "attribute_call":
        return None
    path = loader_path(parts[0])
    if path and len(parts) == 2:
        return path_target(path, res_dir)
    names = [ts.text(p) for p in parts[:-1]]
    if all(p.type == "identifier" for p in parts[:-1]) and is_type_like(names[0]):
        return Target(name=".".join(names))
    return None


def assigned_name(left):
    """`x` for `x = ...` and `self.x = ...`, else ""."""
    if left is None:
        return ""
    if left.type == "identifier":
        return ts.text(left)
    parts = left.named_children if left.type == "attribute" else []
    if len(parts) == 2 and ts.text(parts[0]) == "self" and parts[1].type == "identifier":
        return ts.text(parts[1])
    return ""


def has_annotation(node, name):
    annotations = ts.first_child_of_type(node, "annotations")
    found = ts.children_of_type(annotations, "annotation") if annotations else []
    return any(ts.text(ts.first_child_of_type(a, "identifier")) == name for a in found)


@dataclass
class Var:
    """What a local name stands for: a field, a local / param, or a script (preload name)."""
    role: str  # field, local, alias
    calls_on: object = None  # Target that calls through this name reach, or None


class ClassReader:
    """Reads one class body (the file's top level or an inner class)."""

    def __init__(self, script, decl, body, parent=None):
        self.script, self.decl, self.body, self.parent = script, decl, body, parent
        self.res_dir = script.res_dir
        self.base = None  # ClassReader of a base script (extends by path): its names apply
        self.names = {}  # name -> Var
        self.known = set()  # consts, enums, signals, functions: never type names
        self.functions = set()  # this class's own functions
        self.signals = set()  # this class's own signals
        self.owned = set()  # fields the class assigns `Foo.new()` to
        self.skip_create = set()  # node ids of `Foo.new()` stored in an owned field

    # --- refs ---
    def add(self, kind, target, node, detail="", call="", auto="", from_member=None):
        self.script.result.refs.append(Ref(self.decl.id, target, kind, ts.line(node), detail,
                                           call, scope=self.decl.id, auto=auto,
                                           from_member=from_member))

    def add_types(self, kind, type_node, detail, from_member=None):
        for name in type_names(type_node):
            self.add(kind, Target(name=name), type_node, detail, from_member=from_member)

    def lookup(self, name):
        found = self.names.get(name) or (self.base.lookup(name) if self.base else None)
        if found:
            return found
        outer = self.parent.lookup(name) if self.parent else None
        return outer if outer and outer.role == "alias" else None

    def is_plain_name(self, name):
        return (name in self.known
                or any(r is not None and r.is_plain_name(name) for r in (self.base, self.parent)))

    def is_own_signal_name(self, name):
        """True for a bare name that may be a signal of this class (or a base we cannot see):
        a declared signal, or a name nothing else in sight explains."""
        if self.lookup(name):
            return False
        return name in self.signals or not self.is_plain_name(name)

    def aliases(self):
        """Preload names of this class and its base scripts."""
        found = self.base.aliases() if self.base else {}
        found.update({n: v.calls_on for n, v in self.names.items() if v.role == "alias"})
        return found

    # --- reading ---
    def collect_all(self):
        for statement in self.body.named_children:
            self.collect(statement)
        self.find_owned_fields()

    def read(self):
        self.collect_all()
        for name, target in self.aliases().items():
            self.script.result.aliases.setdefault(name, target)
        for statement in self.body.named_children:
            self.emit(statement)

    def collect(self, node):
        kind = node.type
        name = ts.text(node.child_by_field_name("name"))
        if kind == "constructor_definition":
            self.functions.add(CONSTRUCTOR)
            self.decl.members.append(Member(CONSTRUCTOR, "method", "", ts.line(node)))
        elif kind == "variable_statement":
            self.collect_field(node, name)
        elif kind == "const_statement":
            self.collect_const(node, name)
        elif kind in ("signal_statement", "function_definition", "enum_definition"):
            self.known.add(name)
            if kind == "function_definition":
                self.functions.add(name)
            elif kind == "signal_statement":
                self.signals.add(name)
            member_kind = {"signal_statement": "signal", "function_definition": "method",
                           "enum_definition": "enum"}[kind]
            self.decl.members.append(Member(name, member_kind, "", ts.line(node),
                                            ts.end_line(node)))

    def collect_field(self, node, name):
        type_node = node.child_by_field_name("type")
        value = node.child_by_field_name("value")
        path = loader_path(value)
        if path:
            self.collect_alias(name, path)
        else:
            self.names[name] = Var("field", plain_type(type_node)
                                   or creation_target(value, self.res_dir))
        if creation_target(value, self.res_dir):
            self.owned.add(name)
            self.skip_create.add(value.id)
        self.decl.members.append(Member(name, "field", ts.text(type_node).lstrip(":= "),
                                        ts.line(node)))

    def collect_const(self, node, name):
        path = loader_path(node.child_by_field_name("value"))
        if path:
            self.collect_alias(name, path)
        else:
            self.known.add(name)
        self.decl.members.append(Member(name, "const", "", ts.line(node)))

    def collect_alias(self, name, path):
        self.names[name] = Var("alias", path_target(path, self.res_dir))

    def find_owned_fields(self):
        for node in ts.descendants(self.body, stop=("class_definition",)):
            if node.type != "assignment":
                continue
            name = assigned_name(node.child_by_field_name("left"))
            right = node.child_by_field_name("right")
            created = creation_target(right, self.res_dir)
            field = self.names.get(name)
            if created and field and field.role == "field":
                self.owned.add(name)
                self.skip_create.add(right.id)
                field.calls_on = field.calls_on or created

    def emit(self, node):
        kind = node.type
        if kind == "variable_statement":
            self.emit_field(node)
        elif kind == "class_definition":
            self.script.read_inner(self, node)
        elif kind in ("function_definition", "constructor_definition", "const_statement"):
            Walker(self, member=INIT_MEMBER).walk(node)  # a function renames it

    def emit_field(self, node):
        name = ts.text(node.child_by_field_name("name"))
        type_node = node.child_by_field_name("type")
        value = node.child_by_field_name("value")
        kind = "composition" if name in self.owned else "association"
        detail = "export" if has_annotation(node, "export") else "field"
        if type_names(type_node):
            self.add_types(kind, type_node, detail, FIELD_MEMBER)
        elif creation_target(value, self.res_dir) and not loader_path(value):
            self.add(kind, creation_target(value, self.res_dir), node, detail,
                     from_member=FIELD_MEMBER)
        Walker(self, member=INIT_MEMBER).walk(value)


class Walker:
    """Reads expressions in one function (or a field's value): calls, loads, locals."""

    def __init__(self, cls, names=None, member=None):
        self.cls = cls
        self.names = dict(names or {})  # locals and params
        self.member = member  # enclosing function name for Ref.from_member

    def add(self, *args, **kwargs):
        self.cls.add(*args, from_member=self.member, **kwargs)

    def add_types(self, *args):
        self.cls.add_types(*args, from_member=self.member)

    def lookup(self, name):
        return self.names.get(name) or self.cls.lookup(name)

    def walk(self, node):
        if node is None:
            return
        handler = getattr(self, f"on_{node.type}", None)
        if handler:
            handler(node)
        else:
            self.walk_children(node)

    def walk_children(self, node):
        for child in node.named_children:
            self.walk(child)

    def on_class_definition(self, node):
        pass  # read by its own ClassReader

    def on_function_definition(self, node):
        name = ts.text(node.child_by_field_name("name"))  # a lambda has none: keeps the outer
        if node.type == "constructor_definition":
            name = CONSTRUCTOR
        inner = Walker(self.cls, self.names, name or self.member)
        inner.read_params(node.child_by_field_name("parameters"))
        inner.add_types("dependency", node.child_by_field_name("return_type"), "return")
        inner.walk(node.child_by_field_name("body"))

    on_lambda = on_constructor_definition = on_function_definition

    def read_params(self, params):
        for param in params.named_children if params else []:
            name = ts.text(param if param.type == "identifier"
                           else ts.first_child_of_type(param, "identifier"))
            type_node = param.child_by_field_name("type")
            self.add_types("dependency", type_node, "param")
            self.names[name] = Var("local", plain_type(type_node))
            self.walk(param.child_by_field_name("value"))

    def on_variable_statement(self, node):
        name = ts.text(node.child_by_field_name("name"))
        type_node = node.child_by_field_name("type")
        value = node.child_by_field_name("value")
        path = loader_path(value)
        if path:
            self.names[name] = Var("alias", path_target(path, self.cls.res_dir))
        else:
            self.names[name] = Var("local", plain_type(type_node)
                                   or creation_target(value, self.cls.res_dir))
        self.add_types("dependency", type_node, "local")
        self.walk(value)

    on_const_statement = on_variable_statement

    def on_for_statement(self, node):
        type_node = node.child_by_field_name("type")
        self.add_types("dependency", type_node, "local")
        self.names[ts.text(node.child_by_field_name("left"))] = Var("local", plain_type(type_node))
        self.walk_children(node)

    def on_assignment(self, node):
        name = assigned_name(node.child_by_field_name("left"))
        created = creation_target(node.child_by_field_name("right"), self.cls.res_dir)
        var = self.names.get(name)
        if created and var and var.role == "local" and not var.calls_on:
            var.calls_on = created
        self.walk_children(node)

    def on_binary_operator(self, node):
        if any(child.type in ("as", "is") for child in node.children):
            self.add_types("dependency", node.child_by_field_name("right"), "local")
            self.walk(node.child_by_field_name("left"))
        else:
            self.walk_children(node)

    def on_call(self, node):
        path = loader_path(node)
        if path:
            self.add("dependency", path_target(path, self.cls.res_dir), node, "load")
        callee = node.named_children[0] if node.named_children else None
        if callee is not None and callee.type == "identifier":
            name = ts.text(callee)
            self.add_own_call(name, node)
            if name in STRING_SIGNAL_CALLS and (name == "emit_signal" or string_first_arg(node)):
                self.string_signal(Target(id=self.cls.decl.id), name, node, node)
        self.walk_children(node)

    def add_own_call(self, name, node):
        """A call to one of this class's own functions: an internal call (a self edge)."""
        if name in self.cls.functions:
            self.add("dependency", Target(id=self.cls.decl.id), node, "call", name)

    def on_attribute(self, node):
        parts = node.named_children
        if len(parts) == 2 and ts.text(parts[0]) == "self" and parts[1].type == "attribute_call":
            self.add_own_call(member_name(parts[1]), node)
        if len(parts) > 2 and ts.text(parts[0]) == "self":
            parts = parts[1:]
        target, role, used = self.chain_head(parts)
        rest = parts[used:]
        if target is None:
            target, role, rest = self.untyped_signal_use(parts)
        if target is not None and rest and node.id not in self.cls.skip_create:
            self.chain_use(target, role, rest, node)
        self.walk_children(node)

    def untyped_signal_use(self, parts):
        """(Target, role, parts) for a signal call on something we cannot type: this class's
        own (or inherited) signal, else any Object, so the signal name still counts."""
        last = parts[-1]
        operation = signal_operation(last)
        if operation not in ("emit", "connect", "emit_signal"):
            return None, "", []
        own = (len(parts) == 2 and parts[0].type == "identifier" and operation != "emit_signal"
               and not string_first_arg(last) and self.cls.is_own_signal_name(ts.text(parts[0])))
        if own:
            return Target(id=self.cls.decl.id), "own", parts
        return OBJECT, "untyped", parts[-2:] if operation != "emit_signal" else parts[-1:]

    def chain_head(self, parts):
        """(Target, role, parts used) for what the chain starts on; Target None if unknown."""
        head = parts[0]
        path = loader_path(head)
        if path:
            return path_target(path, self.cls.res_dir), "alias", 1
        if head.type != "identifier":
            return None, "", 1
        name = ts.text(head)
        var = self.lookup(name)
        if var:
            return var.calls_on, var.role, 1
        if self.cls.is_plain_name(name) or not name.lstrip("_")[:1].isupper():
            return None, "", 1
        used = 1
        while (used < len(parts) - 1 and parts[used].type == "identifier"
               and is_type_like(ts.text(parts[used]))):
            used += 1
        return Target(name=".".join(ts.text(p) for p in parts[:used])), "static", used

    def chain_use(self, target, role, rest, node):
        if self.signal_use(target, role, rest, node):
            return
        member = member_name(rest[0])
        if role == "field":
            self.add("association", target, node, "call", member)
        elif role == "local":
            self.add("dependency", target, node, "call", member)
        else:
            detail = "create" if member in CREATORS else "call"
            self.add("dependency", target, node, detail, member)

    def signal_use(self, target, role, rest, node):
        """`...sig.emit()`, `...sig.connect(h)`, `.emit_signal("sig")` and string `connect`
        at the end of a chain. A longer chain than `object.sig.op` has an untyped middle."""
        last = rest[-1]
        operation = signal_operation(last)
        if operation in ("emit_signal", "connect") and string_first_arg(last) or \
                operation == "emit_signal":
            self.string_signal(target if len(rest) == 1 else OBJECT, operation, last, node)
        elif operation in ("emit", "connect") and len(rest) > 1 and rest[-2].type == "identifier":
            owner = target if len(rest) == 2 else OBJECT
            signal = member_name(rest[-2])
            if operation == "connect":
                self.signal_hookup(owner, signal, handler_name(last, self.lookup), node)
            else:
                self.add("association" if role == "field" and owner is target else "dependency",
                         owner, node, EMIT_DETAIL, signal)
        else:
            return False
        return True

    def signal_hookup(self, target, signal, handler, node):
        label = f"{signal}{SIGNAL_ARROW}{handler}"
        self.add("signal", target, node, signal, label, f"signal {label}")

    def string_signal(self, target, function, call, node):
        """`emit_signal("sig")` and Godot 3 / string `connect("sig", ...)`: the name is a
        literal, else ANY_SIGNAL."""
        args = ts.first_child_of_type(call, "arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        signal = string_value(first) if first is not None and first.type == "string" else ANY_SIGNAL
        if function == "emit_signal":
            self.add("dependency", target, node, EMIT_DETAIL, signal)
        else:
            self.signal_hookup(target, signal, handler_name(call, self.lookup, skip=1), node)


def signal_operation(part):
    """`connect` / `emit` / `emit_signal` for a call part; also bare `emit`, as in
    `action = done.emit` (the Callable is passed on and will emit)."""
    if part.type == "attribute_call" or (part.type == "identifier" and ts.text(part) == "emit"):
        return member_name(part)
    return ""


def string_first_arg(call):
    """True when a call's first argument is a string literal (`connect("sig", ...)`)."""
    args = ts.first_child_of_type(call, "arguments")
    return bool(args is not None and args.named_children and args.named_children[0].type == "string")


def member_name(part):
    if part.type == "attribute_call":
        return ts.text(ts.first_child_of_type(part, "identifier"))
    return ts.text(part) if part.type == "identifier" else ""


def handler_name(connect_call, is_variable=None, skip=0):
    """Name of the function passed to `.connect(...)`: `lambda` for an inline func,
    VARIABLE_HANDLER for one held in a variable, `Other.handler` for another object's, and the
    string of `Callable(self, "m")` / Godot 3 `connect("sig", self, "m")`. `skip` leaves out
    leading arguments (the signal name of a string-form connect)."""
    args = ts.first_child_of_type(connect_call, "arguments")
    given = args.named_children[skip:] if args is not None else []
    if not given:
        return ""
    first = given[0]
    if first.type == "lambda":
        return LAMBDA_HANDLER
    if first.type == "call" and ts.text(ts.first_child_of_type(first, "identifier")) == "Callable":
        return callable_method(first)
    if first.type == "attribute":  # Other.handler, or handler.bind(...)
        parts = first.named_children
        if parts[-1].type != "identifier":
            return ts.text(parts[0])
        return ts.text(parts[-1]) if ts.text(parts[0]) == "self" else ts.text(first)
    if first.type == "string":
        return string_value(first)
    if len(given) > 1 and given[1].type == "string":  # Godot 3: connect("sig", self, "m")
        return string_value(given[1])
    name = ts.text(first)
    if first.type != "identifier" or (is_variable and is_variable(name)):
        return VARIABLE_HANDLER  # `hooks[1]`, a Callable in a variable, ...
    return name


def callable_method(call):
    """The method name of `Callable(obj, "method")`, else VARIABLE_HANDLER."""
    args = ts.first_child_of_type(call, "arguments")
    second = args.named_children[1:2] if args is not None else []
    return string_value(second[0]) if second and second[0].type == "string" else VARIABLE_HANDLER


class ScriptReader:
    """Reads one .gd file into a FileResult (or, for a base script, just its names)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.result = FileResult()
        self.res_path = ctx.res_path()
        self.res_dir = posixpath.dirname(self.res_path[len(paths.RES_PREFIX):])
        self.top = None  # ClassReader of the file's top level

    def read(self, names_only=False):
        source = self.ctx.read_text()
        root = PARSER.parse(source.encode("utf-8")).root_node
        class_name = declared_class_name(source)
        decl = Declaration(
            id=script_id(class_name, self.res_path),
            name=class_name or posixpath.basename(self.res_path),
            kind="class" if class_name else "script",
            flags=["abstract"] if top_level_abstract(root) else [],
            file_node=True,
            doc=doc_text.file_doc(root, DOC_MARKERS, HEADER_TYPES, MEMBER_TYPES),
        )
        self.result.declarations.append(decl)
        self.top = ClassReader(self, decl, root)
        self.read_class(self.top, top_extends(root), names_only)
        return self.result

    def read_class(self, cls, extends, names_only=False):
        if names_only:
            cls.base = self.base_reader(extends)
            cls.collect_all()
            return
        if extends is not None:
            self.read_extends(cls, extends)
        cls.read()

    def read_extends(self, cls, node):
        written = node.named_children[0] if node.named_children else None
        if written is None:
            return
        if written.type == "string":
            target = path_target(string_value(written), self.res_dir)
        else:
            names = type_names(written)
            if not names:
                return
            target = Target(name=names[0])
        cls.decl.base = cls.decl.base or target.path or target.name
        cls.base = self.base_reader(node)
        cls.add("generalization", target, node)

    def base_reader(self, extends):
        """ClassReader with the names of a base script given by path, or None."""
        written = extends.named_children[0] if extends and extends.named_children else None
        if written is None or written.type != "string":
            return None
        target = path_target(string_value(written), self.res_dir)
        rel = paths.normalize_ref_path(target.path, self.ctx.rel_path, self.ctx.root)
        return base_script(self.ctx.root, rel)

    def read_inner(self, outer, node):
        name = ts.text(node.child_by_field_name("name"))
        decl = Declaration(
            id=child_id(outer.decl.id, name), name=name, kind="inner", line=ts.line(node),
            end_line=ts.end_line(node),
            flags=["abstract"] if has_annotation(node, "abstract") else [],
            doc=doc_text.doc_above(node, DOC_MARKERS),
        )
        self.result.declarations.append(decl)
        outer.decl.members.append(Member(name, "class", "", ts.line(node)))
        reader = ClassReader(self, decl, node.child_by_field_name("body"), parent=outer)
        self.read_class(reader, node.child_by_field_name("extends"))


_base_scripts = {}  # (root, rel, mtime) -> ClassReader; None while loading (cycle guard)
_base_dependencies = {}  # same key -> rel paths that script's names depended on


def base_script(root, rel):
    """Names (fields, preload names, consts) of a base script, read once per file version."""
    path = Path(root) / rel
    record_dependency(rel)  # even when missing: the file may appear later
    if not rel.endswith(".gd") or not path.is_file():
        return None
    key = (str(root), rel, path.stat().st_mtime_ns)
    if key not in _base_scripts:
        _base_scripts[key] = None
        with track_dependencies() as found:
            reader = ScriptReader(FileContext(Path(root), rel))
            reader.read(names_only=True)
        _base_scripts[key] = reader.top
        _base_dependencies[key] = found
    record_dependency(*_base_dependencies.get(key, ()))
    return _base_scripts[key]


def top_extends(root):
    """The file's extends_statement: on its own line or after `class_name X`."""
    class_stmt = ts.first_child_of_type(root, "class_name_statement")
    inline = class_stmt.child_by_field_name("extends") if class_stmt else None
    return inline or ts.first_child_of_type(root, "extends_statement")


def top_level_abstract(root):
    """`@abstract` standing on its own at the top (before class_name), not on a func."""
    return any(ts.text(ts.first_child_of_type(a, "identifier")) == "abstract"
               for a in ts.children_of_type(root, "annotation"))


@register
class GDScriptModule(LanguageModule):
    name = "gdscript"
    patterns = ("*.gd",)

    def parse(self, ctx):
        return ScriptReader(ctx).read()

    def start_scan(self):
        _base_scripts.clear()
        _base_dependencies.clear()
