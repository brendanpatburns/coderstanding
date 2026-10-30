"""C# reader (docs/DESIGN.md 4.2): declarations, type references, Godot C# idioms.

Only reads files; the shared resolver resolves names and builds edges.
"""

import posixpath
import re
from dataclasses import dataclass, field, replace

import tree_sitter_c_sharp

from coderstanding.language import (FIELD_MEMBER, INIT_MEMBER, Declaration, FileResult, LanguageModule, Ref,
                                  Target, register)
from coderstanding.model import (ANY_SIGNAL, EMIT_DETAIL, SIGNAL_ARROW, Member, add_unique, child_id,
                                 entry_flags)

from . import doc_text
from .ts_util import children_of_type, descendants, first_child_of_type, end_line, line, make_parser, text

PARSER = make_parser(tree_sitter_c_sharp.language())

TYPE_KINDS = {
    "class_declaration": "class", "interface_declaration": "interface",
    "struct_declaration": "struct", "record_declaration": "record",
    "record_struct_declaration": "record", "enum_declaration": "enum",
}
METHOD_NODES = ("method_declaration", "constructor_declaration", "operator_declaration",
                "destructor_declaration", "conversion_operator_declaration")
CREATION_NODES = ("object_creation_expression", "implicit_object_creation_expression")
SINGLETON_NAMES = ("Instance", "Singleton")
LOADERS = ("GD", "ResourceLoader")
SIGNAL_SUFFIX = "EventHandler"
SIGNAL_CALLS = ("EmitSignal", "Connect")
DOC_MARKERS = ("///", "/**")
SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
TAG_RE = re.compile(r"</?\w+[^>]*>")


# ---- type names -------------------------------------------------------------------------

def type_names(node):
    """Type names inside a type node, generics and arrays stripped: `Dictionary<K, Foo>[]`
    gives Dictionary, K, Foo. Predefined types (int, string) give nothing."""
    if node is None:
        return []
    if node.type == "identifier":
        return [text(node)]
    if node.type == "qualified_name":
        return [dotted_name(node)] + generic_arguments(node)
    if node.type == "generic_name":
        return [text(node.children[0])] + generic_arguments(node)
    return [name for child in node.children for name in type_names(child)]


def dotted_name(node):
    """`A.B<C>.D` -> "A.B.D"."""
    if node.type == "generic_name":
        return text(node.children[0])
    if node.type == "qualified_name":
        return ".".join(dotted_name(child) for child in node.children if child.type != ".")
    return text(node)


def generic_arguments(node):
    names = []
    for found in descendants(node):
        if found.type == "type_argument_list":
            names += [n for child in found.children for n in type_names(child)]
    return names


def generic_parameter_names(node):
    """Names declared by a type_parameter_list on `node` ("T", "TKey")."""
    params = first_child_of_type(node, "type_parameter_list")
    if params is None:
        return set()
    return {text(p.children[-1]) for p in children_of_type(params, "type_parameter")}


def modifiers(node):
    return {text(m) for m in children_of_type(node, "modifier")}


def is_creation(node):
    return node is not None and node.type in CREATION_NODES


# ---- reference sink ---------------------------------------------------------------------

@dataclass
class Sink:
    """Collects Refs for one owner type; `generics` are type parameters that are not types."""
    owner: str
    refs: list
    generics: set = field(default_factory=set)
    member: str = None  # enclosing member name for from_member

    def add(self, kind, names, node, detail="", call="", auto=""):
        for name in names:
            if name and name not in self.generics:
                self.refs.append(Ref(self.owner, Target(name=name), kind, line(node), detail,
                                     call, scope=self.owner, auto=auto, from_member=self.member))

    def add_target(self, kind, target, node, detail="", call="", auto=""):
        self.refs.append(Ref(self.owner, target, kind, line(node), detail, call,
                             scope=self.owner, auto=auto, from_member=self.member))

    def with_generics(self, names):
        return replace(self, generics=self.generics | names)

    def in_member(self, name):
        return replace(self, member=name)


# ---- method bodies ----------------------------------------------------------------------

class BodyReader:
    """Reads expressions inside one member: calls, creation, locals, events, Godot loads."""

    def __init__(self, sink, fields, local_types):
        self.sink = sink
        self.fields = fields  # held member name -> type names
        self.locals = dict(local_types)  # parameter / local name -> primary type name

    def visit(self, node):
        handler = getattr(self, f"_{node.type}", None)
        if handler and handler(node) is False:
            return
        for child in node.children:
            self.visit(child)

    def _variable_declaration(self, node):
        type_node = node.child_by_field_name("type")
        explicit = type_node is not None and type_node.type != "implicit_type"
        if explicit:
            self.sink.add("dependency", type_names(type_node), node, "local")
        for declarator in children_of_type(node, "variable_declarator"):
            inferred = type_names(type_node)[:1] if explicit else self._infer(declarator)
            if inferred:
                self.locals[text(declarator.children[0])] = inferred[0]

    def _infer(self, declarator):
        """Type of a `var x = ...` from `new T()` or `Get<T>()`."""
        value = declarator.children[-1] if declarator.named_child_count > 1 else None
        if value is not None and value.type == "equals_value_clause":
            value = value.named_children[-1]
        if value is None:
            return []
        if value.type == "object_creation_expression":
            return type_names(value.child_by_field_name("type"))[:1]
        if value.type == "invocation_expression":
            return generic_arguments(value.child_by_field_name("function"))[:1]
        return []

    def _object_creation_expression(self, node):
        if not self._stored_in_field(node):
            self.sink.add("dependency", type_names(node.child_by_field_name("type")), node,
                          "create")

    def _stored_in_field(self, node):
        parent = node.parent
        return (parent is not None and parent.type == "assignment_expression"
                and self._member_name(parent.child_by_field_name("left")) in self.fields)

    @staticmethod
    def _member_name(node):
        """`_x` or `this._x` -> "_x", otherwise ""."""
        if node is None:
            return ""
        if node.type == "identifier":
            return text(node)
        is_this = node.type == "member_access_expression" and \
            node.child_by_field_name("expression").type == "this_expression"
        return text(node.child_by_field_name("name")) if is_this else ""

    def _assignment_expression(self, node):
        operator = node.child_by_field_name("operator")
        left = node.child_by_field_name("left")
        if text(operator) == "+=" and left.type == "member_access_expression":
            self._event_subscription(node, left)
        elif text(operator) == "+=" and left.type == "identifier" and text(left) not in self.locals:
            self._own_event_subscription(node, text(left))

    def _event_subscription(self, node, left):
        source = self._type_of(left.child_by_field_name("expression"))
        if source:
            event = text(left.child_by_field_name("name"))
            handler = text(node.child_by_field_name("right"))
            self.sink.add("signal", [source], node, event, auto=f"{event} -> {handler}")

    def _own_event_subscription(self, node, event):
        """`Hit += OnHit;` inside the class that declares the signal."""
        handler = text(node.child_by_field_name("right"))
        self.sink.add_target("signal", Target(id=self.sink.owner), node, event,
                             f"{event}{SIGNAL_ARROW}{handler}", f"{event} -> {handler}")

    def _type_of(self, expression):
        """Primary type name of a variable or held member, "" if unknown."""
        if expression is None or expression.type != "identifier":
            return ""
        name = text(expression)
        if name in self.locals:
            return self.locals[name]
        return self.fields[name][0] if self.fields.get(name) else ""

    def _invocation_expression(self, node):
        function = node.child_by_field_name("function")
        target, method = self._split_call(function)
        if target is not None and text(target) in LOADERS and dotted_name(method) == "Load":
            self._load_call(node)
        elif method.type == "generic_name":
            self._generic_call(node, target, method)
        elif text(method) in SIGNAL_CALLS:
            self._signal_call(node, target, text(method))
        else:
            self._plain_call(node, target, text(method))

    @staticmethod
    def _split_call(function):
        if function.type == "member_access_expression":
            return function.child_by_field_name("expression"), function.child_by_field_name("name")
        return None, function

    def _signal_call(self, node, target, function):
        """`EmitSignal(SignalName.Hit)` / `Connect("hit", ...)` on this class or a typed
        variable: an emit ref (call = signal name) or a signal ref."""
        owner = self._signal_owner(target)
        if owner is None:
            return
        arguments = self._arguments(node)
        signal = self._signal_name(arguments[0]) if arguments else ANY_SIGNAL
        if function == "EmitSignal":
            self.sink.add_target("dependency", owner, node, EMIT_DETAIL, signal)
        else:
            handler = text(arguments[1]) if len(arguments) > 1 else ""
            self.sink.add_target("signal", owner, node, signal, f"{signal}{SIGNAL_ARROW}{handler}")

    def _signal_owner(self, target):
        """Target of the object a signal call is made on: this class, or a typed variable."""
        if target is None or target.type == "this_expression":
            return Target(id=self.sink.owner)
        held = self.fields.get(text(target)) if target.type == "identifier" else None
        name = held[0] if held else self._type_of(target)
        return Target(name=name) if name else None

    @staticmethod
    def _arguments(node):
        arglist = first_child_of_type(node, "argument_list")
        return [a.named_children[0] for a in children_of_type(arglist, "argument")
                if a.named_children] if arglist else []

    @staticmethod
    def _signal_name(argument):
        """`"Hit"`, `nameof(Hit)`, `SignalName.Hit` -> "Hit"; anything else ANY_SIGNAL."""
        if argument.type == "string_literal":
            return text(argument).strip('"')
        if argument.type == "member_access_expression":
            return text(argument.child_by_field_name("name"))
        if argument.type == "invocation_expression":
            inner = BodyReader._arguments(argument)
            return text(inner[0]).rpartition(".")[2] if inner else ANY_SIGNAL
        return ANY_SIGNAL

    def _generic_call(self, node, target, method):
        name = text(method.children[0])
        self.sink.add("dependency", generic_arguments(method), node, "call", name)
        self._plain_call(node, target, name)

    def _load_call(self, node):
        arguments = first_child_of_type(node, "argument_list")
        first = arguments.named_children[0] if arguments and arguments.named_children else None
        path = text(first).strip('"') if first is not None else ""
        if path.startswith("res://"):
            self.sink.add_target("dependency", Target(path=path), node, "load")

    def _plain_call(self, node, target, member):
        if target is None:
            return
        held = self.fields.get(text(target)) if target.type == "identifier" else None
        if held:
            self.sink.add("association", held[:1], node, "call", member)
            return
        local = self._type_of(target)
        static = dotted_name(target) if self._is_type_like(target) else ""
        self.sink.add("dependency", [local or static], node, "call", member)

    @staticmethod
    def _is_type_like(node):
        """A name chain starting upper-case (`Util`, `Ns.Util`): a static call target."""
        if node.type == "identifier":
            return text(node)[:1].isupper()
        if node.type == "member_access_expression":
            return BodyReader._is_type_like(node.child_by_field_name("expression"))
        return False

    def _local_function_statement(self, node):
        read_signature(self.sink, node, self)


def read_signature(sink, node, body_reader):
    """Parameter and return types of a method-like node, then its body."""
    sink = sink.with_generics(generic_parameter_names(node))
    body_reader.sink = sink
    returns = node.child_by_field_name("returns") or node.child_by_field_name("type")
    sink.add("dependency", type_names(returns), node, "return")
    parameters = node.child_by_field_name("parameters")
    for parameter in children_of_type(parameters, "parameter") if parameters else []:
        type_node = parameter.child_by_field_name("type")
        sink.add("dependency", type_names(type_node), parameter, "param")
        names = type_names(type_node)
        if names:
            body_reader.locals[text(parameter.child_by_field_name("name"))] = names[0]
    for child in node.children:
        if child.type not in ("parameter_list", "type_parameter_list"):
            body_reader.visit(child)


# ---- types ------------------------------------------------------------------------------

class TypeReader:
    """Reads one type declaration (and its nested types) into declarations and refs."""

    def __init__(self, result, node, namespace, parent_id):
        self.result = result
        self.node = node
        name = text(node.child_by_field_name("name"))
        self.id = child_id(parent_id or namespace, name) if (parent_id or namespace) else name
        self.sink = Sink(self.id, result.refs, generic_parameter_names(node))
        self.body = self._body()
        self.fields = {}  # held member name -> type names
        self.created = self._created_members()
        self.decl = Declaration(
            id=self.id, name=name, kind=self._kind(parent_id), line=line(node),
            end_line=end_line(node),
            flags=self._flags(), namespace=namespace, file_node=False, doc=self._doc())

    def _doc(self):
        """Text of the `/// <summary>` (or plain `///` / `/** */`) above the type."""
        raw = doc_text.comments_above(self.node)
        if not raw or not all(doc_text.is_doc_comment(r, DOC_MARKERS) for r in raw):
            return ""
        lines = [l for r in raw for l in doc_text.comment_lines(r)]
        summary = SUMMARY_RE.search("\n".join(lines))
        return doc_text.trim_doc(TAG_RE.sub("", summary.group(1) if summary else "\n".join(lines)).splitlines())

    def _kind(self, parent_id):
        kind = TYPE_KINDS[self.node.type]
        return "inner" if parent_id and kind == "class" else kind

    def _flags(self):
        found = modifiers(self.node)
        return [flag for flag in ("abstract", "static", "partial") if flag in found]

    def _body(self):
        return first_child_of_type(self.node, "declaration_list", "enum_member_declaration_list")

    def _created_members(self):
        """Names of members assigned `new T()` anywhere in this type (not nested types)."""
        names = set()
        if self.body is None:
            return names
        for found in descendants(self.body, stop=tuple(TYPE_KINDS)):
            if found.type == "assignment_expression" and is_creation(
                    found.child_by_field_name("right")):
                names.add(BodyReader._member_name(found.child_by_field_name("left")))
        return names

    def read(self):
        self.result.declarations.append(self.decl)
        self._register_global_class()
        self._read_bases()
        self._read_primary_constructor()
        if self.body is not None:
            self._index_fields()
            for member in self.body.children:
                self._read_member(member)
        return self.decl

    def _register_global_class(self):
        """Godot shows a `[GlobalClass]` C# class to GDScript by its bare name."""
        attributes = children_of_type(self.node, "attribute_list")
        if any(re.search(r"\bGlobalClass\b", text(a)) for a in attributes):
            self.result.global_aliases[self.decl.name] = Target(id=self.id)

    def _read_bases(self):
        bases = first_child_of_type(self.node, "base_list")
        for base in bases.named_children if bases else []:
            names = type_names(base)
            self.decl.base = self.decl.base or (names[0] if names else "")
            self.sink.add("generalization", names[:1], base)
            self.sink.add("dependency", names[1:], base, "generic")

    def _read_primary_constructor(self):
        parameters = first_child_of_type(self.node, "parameter_list")
        for parameter in children_of_type(parameters, "parameter") if parameters else []:
            names = type_names(parameter.child_by_field_name("type"))
            name = text(parameter.child_by_field_name("name"))
            self.fields[name] = names
            self.sink.in_member(FIELD_MEMBER).add("association", names, parameter, "field")
            self.decl.members.append(Member(name, "property", text(
                parameter.child_by_field_name("type")), line(parameter)))

    def _index_fields(self):
        """Record each field/property name with its type, so calls through it are found."""
        for member in self.body.children:
            if member.type == "field_declaration":
                declaration = first_child_of_type(member, "variable_declaration")
                names = type_names(declaration.child_by_field_name("type"))
                for declarator in children_of_type(declaration, "variable_declarator"):
                    self.fields[text(declarator.children[0])] = names
            elif member.type == "property_declaration":
                self.fields[text(member.child_by_field_name("name"))] = type_names(
                    member.child_by_field_name("type"))

    def _read_member(self, member):
        if member.type in TYPE_KINDS:
            TypeReader(self.result, member, self.decl.namespace, self.id).read()
        elif member.type == "field_declaration":
            self._read_field(member)
        elif member.type == "event_field_declaration":
            self._read_event_field(member)
        elif member.type == "property_declaration":
            self._read_property(member)
        elif member.type in METHOD_NODES:
            self._read_method(member)
        elif member.type == "delegate_declaration":
            self._read_delegate(member)

    def _holding_kind(self, name, initializer):
        return "composition" if is_creation(initializer) or name in self.created \
            else "association"

    def _read_field(self, node):
        declaration = first_child_of_type(node, "variable_declaration")
        type_node = declaration.child_by_field_name("type")
        for declarator in children_of_type(declaration, "variable_declarator"):
            name = text(declarator.children[0])
            initializer = declarator.named_children[-1] if declarator.named_child_count > 1 \
                else None
            self._hold(node, name, "field", type_node, initializer)
            self._read_initializer(initializer)

    def _read_event_field(self, node):
        declaration = first_child_of_type(node, "variable_declaration")
        type_node = declaration.child_by_field_name("type")
        for declarator in children_of_type(declaration, "variable_declarator"):
            name = text(declarator.children[0])
            self.decl.members.append(Member(name, "event", text(type_node), line(node)))
            self.sink.in_member(FIELD_MEMBER).add("association", type_names(type_node), node, "field")

    def _read_property(self, node):
        name = text(node.child_by_field_name("name"))
        type_node = node.child_by_field_name("type")
        initializer = node.child_by_field_name("value")
        self._hold(node, name, "property", type_node, initializer)
        self._read_initializer(initializer)
        for child in node.children:
            if child.type in ("accessor_list", "arrow_expression_clause"):
                self._body_reader(name).visit(child)

    def _hold(self, node, name, kind, type_node, initializer):
        self.decl.members.append(Member(name, kind, text(type_node), line(node)))
        self.sink.in_member(FIELD_MEMBER).add(self._holding_kind(name, initializer),
                                       type_names(type_node), node, "field")
        if "static" in modifiers(node) and name in SINGLETON_NAMES and \
                type_names(type_node)[:1] == [self.decl.name]:
            self.decl.flags.append("singleton")

    def _read_initializer(self, initializer):
        """Read calls inside an initializer; a plain `new T()` is already the composition."""
        if initializer is not None and not is_creation(initializer):
            self._body_reader(INIT_MEMBER).visit(initializer)

    def _body_reader(self, member):
        return BodyReader(self.sink.in_member(member), self.fields, {})

    def _read_method(self, node):
        returns = node.child_by_field_name("returns") or node.child_by_field_name("type")
        name = text(node.child_by_field_name("name")) or self.decl.name
        self.decl.members.append(Member(name, "method", text(returns), line(node), end_line(node)))
        if name == "Main" and "static" in modifiers(node):
            add_unique(self.decl.flags, entry_flags("program-main"))
        reader = self._body_reader(name)
        read_signature(reader.sink, node, reader)

    def _read_delegate(self, node):
        name = text(node.child_by_field_name("name"))
        if any("Signal" in text(a) for a in children_of_type(node, "attribute_list")):
            signal = name[:-len(SIGNAL_SUFFIX)] if name.endswith(SIGNAL_SUFFIX) else name
            self.decl.members.append(Member(signal, "signal", "", line(node)))


# ---- file -------------------------------------------------------------------------------

@register
class CSharpModule(LanguageModule):
    name = "csharp"
    patterns = ("*.cs",)
    # Global and implicit usings live in other files, so let unknown names match any ".Name".
    loose_names = True

    def parse(self, ctx):
        tree = PARSER.parse(ctx.read_bytes())
        result = FileResult()
        self._read_scope(result, tree.root_node.children, "")
        self._mark_file_node(result)
        if any(n.type == "global_statement" for n in tree.root_node.children):
            result.declarations.append(self._top_level_program(ctx, not result.declarations))
        return result

    @staticmethod
    def _top_level_program(ctx, is_file_node):
        """A file of top-level statements is itself the program: a module node."""
        return Declaration(ctx.rel_path, posixpath.basename(ctx.rel_path), "module",
                           flags=entry_flags("program-main"), file_node=is_file_node)

    def _read_scope(self, result, nodes, namespace):
        for node in nodes:
            if node.type == "using_directive":
                self._read_using(result, node)
            elif node.type == "file_scoped_namespace_declaration":
                namespace = self._join(namespace, dotted_name(node.child_by_field_name("name")))
            elif node.type == "namespace_declaration":
                inner = self._join(namespace, dotted_name(node.child_by_field_name("name")))
                body = first_child_of_type(node, "declaration_list")
                self._read_scope(result, body.children if body else [], inner)
            elif node.type in TYPE_KINDS:
                TypeReader(result, node, namespace, "").read()

    @staticmethod
    def _join(namespace, name):
        return f"{namespace}.{name}" if namespace else name

    @staticmethod
    def _read_using(result, node):
        names = [c for c in node.named_children if c.type in ("identifier", "qualified_name")]
        is_alias = any(c.type == "=" for c in node.children)
        if is_alias and len(names) == 2:
            result.aliases[text(names[0])] = Target(name=dotted_name(names[1]))
        elif names:
            result.imports.append(dotted_name(names[-1]))

    @staticmethod
    def _mark_file_node(result):
        """The first declaration is the first top-level type: what a path reference means."""
        if result.declarations:
            result.declarations[0].file_node = True
