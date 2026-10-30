"""Kotlin reader (docs/DESIGN.md 4.4d): declarations, type references, calls, Kotlin idioms.

Only reads files; the shared resolver resolves names and builds edges. Ids are full names
(`com.example.ChatService`, nested `Outer.Inner`) like Java's, so a Java reader links through them.
"""

import posixpath
from dataclasses import dataclass, field, replace

import tree_sitter_kotlin

from coderstanding.language import (FIELD_MEMBER, INIT_MEMBER, Declaration, FileResult, LanguageModule, Ref,
                                  Target, register)
from coderstanding.model import Member, add_unique, child_id, entry_flags

from . import doc_text
from .ts_util import children_of_type, descendants, end_line, first_child_of_type, line, make_parser, text

PARSER = make_parser(tree_sitter_kotlin.language())

TYPE_NODES = ("class_declaration", "object_declaration", "companion_object")
TYPE_REFS = ("user_type", "nullable_type", "function_type", "parenthesized_type")
BUILTIN_TYPES = {"Int", "Long", "Short", "Byte", "Float", "Double", "Boolean", "Char", "String",
                 "Unit", "Any", "Nothing", "Array", "UInt", "ULong", "UShort", "UByte"}
INJECTORS = ("get", "inject", "viewModel", "viewModels", "hiltViewModel", "koinInject")
GRADLE_SCRIPT_SUFFIX = ".gradle.kts"  # build scripts: the stack reader reads them, not this one
APPLICATION_BASES = ("Application", "MultiDexApplication")
DOC_MARKERS = ("/**",)


# ---- type names -------------------------------------------------------------------------

def type_names(node):
    """Type names inside a type node, generics and nullability stripped: `Map<K, Foo?>?` gives
    Map, K, Foo. Built-in types (Int, String, Unit) give nothing."""
    if node is None:
        return []
    if node.type == "user_type":
        name = ".".join(text(c) for c in children_of_type(node, "identifier"))
        names = [] if name in BUILTIN_TYPES else [name]
        for arguments in children_of_type(node, "type_arguments"):
            names += type_names(arguments)
        return names
    return [name for child in node.children for name in type_names(child)]


def type_node_of(node):
    """The declared type among a declaration's children (after the `:`), None if not written."""
    return first_child_of_type(node, *TYPE_REFS)


def declared_name(node):
    """Name of a declaration: its `name` field, else its first identifier."""
    named = node.child_by_field_name("name") or first_child_of_type(node, "identifier")
    return text(named)


def modifier_words(node):
    found = first_child_of_type(node, "modifiers")
    return set(text(found).replace("@", " ").split()) if found is not None else set()


def is_construction(node):
    """`Foo(...)`: a call whose callee is an upper-case name (Kotlin has no `new`)."""
    return node is not None and node.type == "call_expression" and \
        node.children[0].type == "identifier" and text(node.children[0])[:1].isupper()


def head_of(node):
    """First name of a `A.b.c` chain, `this` for `this.b`, else None."""
    while node is not None and node.type in ("navigation_expression", "unary_expression"):
        node = node.child_by_field_name("argument") if node.type == "unary_expression" \
            else node.children[0]
    return node


def held_name(node):
    """`x`, `x!!` or `this.x` -> "x", otherwise ""."""
    if node is None:
        return ""
    if node.type == "unary_expression":
        return held_name(node.child_by_field_name("argument"))
    if node.type == "identifier":
        return text(node)
    if node.type == "navigation_expression" and node.children[0].type == "this_expression":
        return text(node.children[-1])
    return ""


# ---- reference sink ---------------------------------------------------------------------

@dataclass
class Sink:
    """Collects Refs for one owner; `generics` are type parameters that are not types."""
    owner: str
    refs: list
    generics: set = field(default_factory=set)
    member: str = None  # enclosing member name for from_member

    def add(self, kind, names, node, detail="", call=""):
        for name in names:
            if name and name not in self.generics:
                self.refs.append(Ref(self.owner, Target(name=name), kind, line(node), detail,
                                     call, scope=self.owner, from_member=self.member))

    def with_generics(self, names):
        return replace(self, generics=self.generics | names)

    def in_member(self, name):
        return replace(self, member=name)


def generic_parameter_names(node):
    params = first_child_of_type(node, "type_parameters")
    if params is None:
        return set()
    return {declared_name(p) for p in children_of_type(params, "type_parameter")}


# ---- bodies -----------------------------------------------------------------------------

class BodyReader:
    """Reads expressions inside one member: constructor calls, calls, types, static uses."""

    def __init__(self, sink, fields, locals_=None, stored=()):
        self.sink = sink
        self.fields = fields  # held member name -> type names
        self.locals = dict(locals_ or {})  # parameter / local name -> primary type name
        self.stored = set(stored)  # node ids of constructor calls a property already holds
        self.call_targets = set()  # node ids of call callees (not a static use by themselves)

    def visit(self, root):
        stack = [root]
        while stack:
            node = stack.pop()
            handler = getattr(self, f"_{node.type}", None)
            if handler and handler(node) is False:
                continue
            stack.extend(reversed(node.children))

    def _user_type(self, node):
        self.sink.add("dependency", type_names(node), node, "local")
        return False

    def _property_declaration(self, node):
        variable = first_child_of_type(node, "variable_declaration")
        if variable is None:
            return
        names = type_names(type_node_of(variable)) or infer_types(node)
        if names:
            self.locals[declared_name(variable)] = names[0]

    def _call_expression(self, node):
        callee = node.children[0]
        self.call_targets.add(callee.id)
        if callee.type == "identifier":
            if is_construction(node) and node.id not in self.stored and not self._stores(node):
                self.sink.add("dependency", [text(callee)], node, "create")
        elif callee.type == "navigation_expression":
            self._qualified_call(node, callee)

    def _stores(self, node):
        """True for `x = Foo()` where x is a held member (the holding edge already says it)."""
        parent = node.parent
        return parent is not None and parent.type == "assignment" and \
            held_name(parent.child_by_field_name("left")) in self.fields

    def _qualified_call(self, node, callee):
        member = text(callee.children[-1])
        target = callee.children[0]
        if member[:1].isupper() and head_of(target) is not None and head_of(target).type == "identifier":
            self.sink.add("dependency", [text(callee)], node, "create")  # `pkg.Foo()`
            return
        name = held_name(target)
        if name in self.locals:
            self.sink.add("dependency", [self.locals[name]], node, "call", member)
        elif name in self.fields:
            self.sink.add("association", self.fields[name][:1], node, "call", member)
        else:
            self._static_use(node, target, "call", member)

    def _navigation_expression(self, node):
        if node.id not in self.call_targets:
            self._static_use(node, node.children[0], "ref", "")

    def _static_use(self, node, target, detail, member):
        """`Util.helper()`, `Color.RED`, `Foo.Companion.make()`: an upper-case head is a type."""
        head = head_of(target)
        if head is not None and head.type == "identifier" and text(head)[:1].isupper() \
                and text(head) not in self.locals and text(head) not in self.fields:
            self.sink.add("dependency", [text(head)], node, detail, member)


def infer_types(node):
    """Type of an untyped property from `= Foo()`, `= get<Foo>()` or `by lazy { Foo() }`."""
    value = initializer_of(node)
    delegate = first_child_of_type(node, "property_delegate")
    if delegate is not None:
        value = next((n for n in descendants(delegate) if n.type == "call_expression"
                      and (is_construction(n) or is_injection(n))), None)
    if value is None or value.type != "call_expression":
        return []
    if is_construction(value):
        return [text(value.children[0])]
    return type_names(first_child_of_type(value, "type_arguments"))[:1]


def held_construction(initializer, delegate):
    """The `Foo()` a property creates for itself: its initializer, or the first in `by lazy {}`."""
    if is_construction(initializer):
        return initializer
    found = descendants(delegate) if delegate is not None else ()
    return next((n for n in found if is_construction(n)), None)


def is_injection(node):
    """`inject<Foo>()` / `get<Foo>()`: a call that hands out a Foo."""
    return node.children[0].type == "identifier" and text(node.children[0]) in INJECTORS \
        and first_child_of_type(node, "type_arguments") is not None


def initializer_of(node):
    """The expression after `=` in a property declaration, None if there is none."""
    kinds = [c.type for c in node.children]
    if "=" not in kinds:
        return None
    return node.children[kinds.index("=") + 1]


# ---- owners (a type, or a file's top level) ---------------------------------------------

class Owner:
    """Reads the members of one owner: a type body or a file's top level (the module node)."""

    def __init__(self, result, decl, generics=()):
        self.result = result
        self.decl = decl
        self.sink = Sink(decl.id, result.refs, set(generics))
        self.fields = {}  # held member name -> type names
        self.init_locals = {}  # plain constructor parameters, visible in initializers
        self.created = set()  # member names assigned `Foo()` somewhere
        self.jvm_main = False  # holds a `@JvmStatic fun main`: its class (or outer class) starts

    def index(self, nodes, scan_root):
        """Find held members and the ones assigned a new object, so calls through them work."""
        for node in nodes:
            if node.type == "property_declaration":
                variable = first_child_of_type(node, "variable_declaration")
                names = type_names(type_node_of(variable)) or infer_types(node) if variable else []
                if names:
                    self.fields[declared_name(variable)] = names
        for found in descendants(scan_root, stop=TYPE_NODES):
            if found.type == "assignment" and is_construction(found.child_by_field_name("right")):
                self.created.add(held_name(found.child_by_field_name("left")))

    def body_reader(self, member, stored=()):
        return BodyReader(self.sink.in_member(member), self.fields, self.init_locals, stored)

    def read_member(self, node, nested):
        """Read one body child; `nested(node)` reads a type declared inside."""
        if node.type in TYPE_NODES:
            nested(node)
        elif node.type == "property_declaration":
            self.read_property(node)
        elif node.type == "function_declaration":
            self.read_function(node)
        elif node.type == "secondary_constructor":
            self.read_constructor(node)
        elif node.type == "anonymous_initializer":
            self.body_reader(INIT_MEMBER).visit(node)
        elif node.type == "enum_entry":
            self.body_reader(INIT_MEMBER).visit(node)

    # -- properties --

    def read_property(self, node):
        variable = first_child_of_type(node, "variable_declaration")
        if variable is None:  # destructuring
            self.body_reader(INIT_MEMBER).visit(node)
            return
        name = declared_name(variable)
        type_node = type_node_of(variable)
        inferred = [] if type_node is not None else infer_types(node)
        initializer = initializer_of(node)
        delegate = first_child_of_type(node, "property_delegate")
        self.hold(node, name, "property", type_node, type_names(type_node) or inferred,
                  self.is_constructed(name, initializer, delegate))
        made = held_construction(initializer, delegate)
        stored = {made.id} if made is not None else set()
        init_reader = self.body_reader(INIT_MEMBER, stored)
        for part in (initializer, delegate):
            if part is not None:
                init_reader.visit(part)
        for accessor in children_of_type(node, "getter", "setter"):
            self.body_reader(name).visit(accessor)

    def is_constructed(self, name, initializer, delegate):
        """A held object the owner creates itself (composition) vs one handed in (association)."""
        return held_construction(initializer, delegate) is not None or name in self.created

    def hold(self, node, name, kind, type_node, names, constructed):
        shown = text(type_node) if type_node is not None else (names[0] if names else "")
        self.decl.members.append(Member(name, kind, shown, line(node)))
        self.sink.in_member(FIELD_MEMBER).add("composition" if constructed else "association",
                                              names, node, "field")

    # -- functions --

    def read_function(self, node):
        name = declared_name(node)
        kinds = [c.type for c in node.children]
        dot = kinds.index(".") if "." in kinds else -1
        types = [c for c in node.children if c.type in TYPE_REFS]
        receiver = types[0] if dot >= 0 and types else None
        returns = types[-1] if types and types[-1] is not receiver else None
        kind = "function" if self.decl.kind == "module" else "method"
        self.decl.members.append(Member(name, kind, text(returns), line(node), end_line(node)))
        sink = self.sink.in_member(name).with_generics(generic_parameter_names(node))
        sink.add("dependency", type_names(receiver), node, "extension")
        sink.add("dependency", type_names(returns), node, "return")
        if "main" == name and receiver is None and self.decl.kind == "module":
            add_unique(self.decl.flags, entry_flags("program-main"))
        elif name == "main" and "JvmStatic" in modifier_words(node):
            self.jvm_main = True
        self.read_signature_and_body(node, sink, name)

    def read_constructor(self, node):
        self.decl.members.append(Member(self.decl.name, "method", "", line(node), end_line(node)))
        self.read_signature_and_body(node, self.sink.in_member(self.decl.name), self.decl.name)

    def read_signature_and_body(self, node, sink, member):
        reader = BodyReader(sink, self.fields)
        parameters = first_child_of_type(node, "function_value_parameters")
        for parameter in children_of_type(parameters, "parameter") if parameters else []:
            names = type_names(type_node_of(parameter))
            sink.add("dependency", names, parameter, "param")
            if names:
                reader.locals[declared_name(parameter)] = names[0]
        reader.sink = sink
        for child in node.children:
            if child.type not in ("function_value_parameters", "type_parameters", *TYPE_REFS):
                reader.visit(child)


# ---- types ------------------------------------------------------------------------------

class TypeReader(Owner):
    """Reads one type declaration (class, interface, object, companion) and its nested types."""

    def __init__(self, result, node, namespace, parent_id):
        name = "Companion" if node.type == "companion_object" and node.child_by_field_name(
            "name") is None else declared_name(node)
        type_id = child_id(parent_id or namespace, name) if (parent_id or namespace) else name
        self.node = node
        self.parent_id = parent_id
        self.words = modifier_words(node)
        decl = Declaration(id=type_id, name=name, kind=self._kind(), line=line(node),
                           end_line=end_line(node), flags=self._flags(), namespace=namespace,
                           doc=self._doc())
        super().__init__(result, decl, generic_parameter_names(node))
        self.body = first_child_of_type(node, "class_body", "enum_class_body")

    def _kind(self):
        node = self.node
        if node.type != "class_declaration":
            return "inner" if self.parent_id else "class"
        if first_child_of_type(node, "interface") is not None:
            return "interface"
        if "enum" in self.words:
            return "enum"
        if self.words & {"data", "value"}:
            return "record"
        return "inner" if self.parent_id else "class"

    def _flags(self):
        flags = []
        is_interface = self.node.type == "class_declaration" and \
            first_child_of_type(self.node, "interface") is not None
        if self.words & {"abstract", "sealed"} and not is_interface:
            flags.append("abstract")
        if self.node.type == "object_declaration":
            flags.append("singleton")
        if self.node.type == "companion_object":
            flags.append("static")
        return flags

    def _doc(self):
        raw = doc_text.comments_above(self.node)
        if raw and all(doc_text.is_doc_comment(r, DOC_MARKERS) for r in raw):
            return doc_text.comments_text(raw)
        return ""

    def read(self):
        self.result.declarations.append(self.decl)
        self._read_delegation()
        constructor = first_child_of_type(self.node, "primary_constructor")
        if constructor is not None:
            self._read_primary_constructor(constructor)
        members = self._body_members()
        self.index(members, self.body if self.body is not None else self.node)
        for member in members:
            self.read_member(member, self._read_nested)
        if self.jvm_main and self.node.type == "object_declaration":
            add_unique(self.decl.flags, entry_flags("program-main"))
        return self

    def _body_members(self):
        return list(self.body.children) if self.body is not None else []

    def _read_nested(self, node):
        if TypeReader(self.result, node, self.decl.namespace, self.decl.id).read().jvm_main:
            self.jvm_main = True
            if node.type == "companion_object":  # `Main.main` is started through its outer class
                add_unique(self.decl.flags, entry_flags("program-main"))

    def _read_delegation(self):
        specifiers = first_child_of_type(self.node, "delegation_specifiers")
        reader = self.body_reader(INIT_MEMBER)
        for specifier in children_of_type(specifiers, "delegation_specifier") if specifiers else []:
            names = type_names(first_child_of_type(
                first_child_of_type(specifier, "constructor_invocation", "explicit_delegation")
                or specifier, "user_type"))
            self.decl.base = self.decl.base or (names[0] if names else "")
            self.sink.add("generalization", names[:1], specifier)
            self.sink.add("dependency", names[1:], specifier, "generic")
            for part in self._delegation_expressions(specifier):
                reader.visit(part)
            if names and names[0].rpartition(".")[2] in APPLICATION_BASES:
                add_unique(self.decl.flags, entry_flags("program-main"))

    @staticmethod
    def _delegation_expressions(specifier):
        """Constructor arguments of `Base(a, b)` and the object of `Iface by impl`."""
        invocation = first_child_of_type(specifier, "constructor_invocation")
        explicit = first_child_of_type(specifier, "explicit_delegation")
        arguments = first_child_of_type(invocation, "value_arguments") if invocation else None
        return [n for n in (arguments, explicit.children[-1] if explicit else None) if n]

    def _read_primary_constructor(self, constructor):
        parameters = first_child_of_type(constructor, "class_parameters")
        for parameter in children_of_type(parameters, "class_parameter") if parameters else []:
            type_node = type_node_of(parameter)
            names = type_names(type_node)
            name = declared_name(parameter)
            if first_child_of_type(parameter, "val", "var") is not None:
                self.fields[name] = names
                self.hold(parameter, name, "property", type_node, names, False)
            else:
                self.sink.in_member(INIT_MEMBER).add("dependency", names, parameter, "param")
                if names:
                    self.init_locals[name] = names[0]
            default = initializer_of(parameter)
            if default is not None:
                self.body_reader(INIT_MEMBER).visit(default)


# ---- file -------------------------------------------------------------------------------

@register
class KotlinModule(LanguageModule):
    name = "kotlin"
    patterns = ("*.kt", "*.kts")

    def parse(self, ctx):
        result = FileResult()
        if ctx.rel_path.endswith(GRADLE_SCRIPT_SUFFIX):
            return result
        root = PARSER.parse(ctx.read_bytes()).root_node
        namespace = self._read_header(result, root)
        module = Owner(result, self._module_declaration(ctx, namespace))
        top = [n for n in root.children if n.type not in ("package_header", "import")]
        module.index(top, root)
        for node in top:
            self._read_top_level(result, module, node, namespace)
        if ctx.rel_path.endswith(".kts"):
            add_unique(module.decl.flags, entry_flags("script-main"))
        if module.decl.members or module.decl.flags or any(
                r.from_id == module.decl.id for r in result.refs):
            result.declarations.append(module.decl)
        if result.declarations:
            result.declarations[0].file_node = True
        return result

    @staticmethod
    def _module_declaration(ctx, namespace):
        """The file's top-level functions and properties live in one module node (`FooKt`)."""
        name = posixpath.splitext(posixpath.basename(ctx.rel_path))[0] + "Kt"
        return Declaration(child_id(namespace, name) if namespace else name, name, "module",
                           namespace=namespace)

    def _read_top_level(self, result, module, node, namespace):
        if node.type == "type_alias":
            names = type_names(first_child_of_type(node, *TYPE_REFS))
            if names:
                result.aliases[text(node.child_by_field_name("type"))] = Target(name=names[0])
        elif node.type in TYPE_NODES:
            TypeReader(result, node, namespace, "").read()
        elif node.type in ("function_declaration", "property_declaration"):
            module.read_member(node, lambda n: None)
        elif node.type not in ("block_comment", "line_comment", "ERROR", "file_annotation"):
            module.body_reader(INIT_MEMBER).visit(node)

    @staticmethod
    def _read_header(result, root):
        namespace = ""
        for node in root.children:
            if node.type == "package_header":
                namespace = text(first_child_of_type(node, "qualified_identifier", "identifier"))
            elif node.type == "import":
                KotlinModule._read_import(result, node)
        return namespace

    @staticmethod
    def _read_import(result, node):
        path = first_child_of_type(node, "qualified_identifier")
        if path is None:
            return
        full = text(path)
        alias = first_child_of_type(node, "identifier")
        if any(c.type == "*" for c in node.children):
            result.imports.append(full)
        else:
            result.aliases[text(alias) if alias is not None else full.rpartition(".")[2]] = \
                Target(name=full)
