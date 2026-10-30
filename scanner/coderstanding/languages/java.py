"""Java reader (docs/DESIGN.md 4): types, members, type references, imports.

Only reads files; the shared resolver resolves names and builds edges. Ids follow the
same scheme as the Kotlin reader (`package.Class`, `package.Outer.Inner`), so Java and
Kotlin classes in one project link through the shared resolver.
"""

from dataclasses import dataclass, field, replace

import tree_sitter_java

from coderstanding.language import (FIELD_MEMBER, INIT_MEMBER, Declaration, FileResult, LanguageModule, Ref,
                                  Target, register)
from coderstanding.model import Member, add_unique, child_id, entry_flags

from .ts_util import children_of_type, descendants, end_line, first_child_of_type, line, make_parser, text

PARSER = make_parser(tree_sitter_java.language())

TYPE_KINDS = {
    "class_declaration": "class", "interface_declaration": "interface",
    "enum_declaration": "enum", "record_declaration": "record",
    "annotation_type_declaration": "interface",
}
BODY_NODES = ("class_body", "interface_body", "enum_body", "annotation_type_body")
METHOD_NODES = ("method_declaration", "constructor_declaration", "compact_constructor_declaration")
FIELD_NODES = ("field_declaration", "constant_declaration")
# Always in scope in Java, never a dependency worth an edge or an "unresolved" entry.
JAVA_LANG = frozenset((
    "Object", "String", "Integer", "Long", "Short", "Byte", "Double", "Float", "Boolean",
    "Character", "Number", "Void", "Math", "System", "Class", "Enum", "Record", "Iterable",
    "Comparable", "Runnable", "Thread", "Throwable", "Exception", "RuntimeException", "Error",
    "StringBuilder", "CharSequence", "Override", "Deprecated", "FunctionalInterface",
    "SuppressWarnings", "SafeVarargs", "AutoCloseable", "Cloneable"))
PRIMITIVE_TYPES = ("integral_type", "floating_point_type", "boolean_type", "void_type")


# ---- type names -------------------------------------------------------------------------

def type_names(node):
    """Type names inside a type node, arrays, generics and wildcards stripped:
    `Map<K, Foo>[]` gives Map, K, Foo. Primitives and java.lang types give nothing."""
    return [name for name in raw_type_names(node) if name not in JAVA_LANG]


def raw_type_names(node):
    if node is None or node.type in PRIMITIVE_TYPES:
        return []
    if node.type == "type_identifier":
        return [text(node)]
    if node.type == "scoped_type_identifier":
        return [dotted_name(node)] + [n for c in node.children if c.type == "generic_type"
                                      for n in raw_type_names(c)[1:]]
    if node.type == "generic_type":
        head = node.children[0]
        arguments = first_child_of_type(node, "type_arguments")
        return raw_type_names(head) + ([n for c in arguments.named_children
                                        for n in raw_type_names(c)] if arguments else [])
    if node.type == "array_type":
        return raw_type_names(node.child_by_field_name("element"))
    return [name for child in node.named_children if child.type != "annotation"
            and child.type != "marker_annotation" for name in raw_type_names(child)]


def dotted_name(node):
    """`A.B<C>.D` or `a.b.C` -> "A.B.D" / "a.b.C"."""
    if node.type == "generic_type":
        return dotted_name(node.children[0])
    if node.type in ("scoped_type_identifier", "scoped_identifier", "field_access"):
        return ".".join(dotted_name(c) for c in node.named_children
                        if c.type in ("type_identifier", "identifier", "generic_type",
                                      "scoped_type_identifier", "scoped_identifier",
                                      "field_access"))
    return text(node)


def type_parameter_names(node):
    """Names declared by the type_parameters of a class or method ("T", "K")."""
    params = first_child_of_type(node, "type_parameters")
    if params is None:
        return set()
    return {text(first_child_of_type(p, "type_identifier")) for p in children_of_type(params, "type_parameter")}


def type_parameter_bounds(node):
    params = first_child_of_type(node, "type_parameters")
    return [n for p in children_of_type(params, "type_parameter") for b in children_of_type(p, "type_bound")
            for n in type_names(b)] if params else []


def modifier_words(node):
    """Modifier keywords of a declaration ("public", "static", "abstract")."""
    modifiers = first_child_of_type(node, "modifiers")
    return {text(m) for m in modifiers.children if m.type not in ("annotation", "marker_annotation")} \
        if modifiers else set()


def is_creation(node):
    return node is not None and node.type == "object_creation_expression"


# ---- reference sink ---------------------------------------------------------------------

@dataclass
class Sink:
    """Collects Refs for one owner type; `generics` are type parameters that are not types."""
    owner: str
    refs: list
    generics: set = field(default_factory=set)
    member: str = None  # enclosing member name for from_member

    def add(self, kind, names, node, detail="", call=""):
        for name in names:
            if name and name not in self.generics:
                self.refs.append(Ref(self.owner, Target(name=name), kind, line(node), detail,
                                     call, scope=self.owner, from_member=self.member))

    def add_annotations(self, node):
        """`@Autowired`, `@Service(...)` on a declaration or parameter: a dependency each."""
        modifiers = first_child_of_type(node, "modifiers")
        for annotation in children_of_type(modifiers, "annotation", "marker_annotation") if modifiers else []:
            name = dotted_name(annotation.child_by_field_name("name"))
            self.add("dependency", [] if name in JAVA_LANG else [name], annotation, "annotation")

    def with_generics(self, names):
        return replace(self, generics=self.generics | names)

    def in_member(self, name):
        return replace(self, member=name)


# ---- method bodies ----------------------------------------------------------------------

class BodyReader:
    """Reads expressions inside one member: calls, creation, locals, casts.
    Lambdas and anonymous classes are read as part of the enclosing member."""

    def __init__(self, sink, fields, static_calls):
        self.sink = sink
        self.fields = fields  # held member name -> type names
        self.static_calls = static_calls  # statically imported method -> class name
        self.locals = {}  # parameter / local name -> primary type name

    def visit(self, node):
        handler = getattr(self, f"_{node.type}", None)
        if handler and handler(node) is False:
            return
        for child in node.children:
            self.visit(child)

    def _local_variable_declaration(self, node):
        type_node = node.child_by_field_name("type")
        inferred = type_node is not None and text(type_node) == "var"
        if not inferred:
            self.sink.add("dependency", type_names(type_node), node, "local")
        for declarator in children_of_type(node, "variable_declarator"):
            names = self._infer(declarator) if inferred else type_names(type_node)[:1]
            if names:
                self.locals[text(declarator.child_by_field_name("name"))] = names[0]

    @staticmethod
    def _infer(declarator):
        value = declarator.child_by_field_name("value")
        return type_names(value.child_by_field_name("type"))[:1] if is_creation(value) else []

    def _object_creation_expression(self, node):
        if not self._stored_in_field(node):
            self.sink.add("dependency", type_names(node.child_by_field_name("type")), node, "create")

    def _stored_in_field(self, node):
        parent = node.parent
        return (parent is not None and parent.type == "assignment_expression"
                and self._member_name(parent.child_by_field_name("left")) in self.fields)

    @staticmethod
    def _member_name(node):
        """`x` or `this.x` -> "x", otherwise ""."""
        if node is None:
            return ""
        if node.type == "identifier":
            return text(node)
        if node.type == "field_access" and node.child_by_field_name("object").type == "this":
            return text(node.child_by_field_name("field"))
        return ""

    def _method_invocation(self, node):
        target = node.child_by_field_name("object")
        member = text(node.child_by_field_name("name"))
        if target is None:
            if member in self.static_calls:
                self.sink.add("dependency", [self.static_calls[member]], node, "call", member)
            return
        name = self._member_name(target) if target.type != "identifier" else text(target)
        held = self.fields.get(name) if (target.type == "identifier" or name) else None
        if held and name not in self.locals:
            self.sink.add("association", held[:1], node, "call", member)
            return
        local = self.locals.get(name) if target.type == "identifier" else ""
        static = dotted_name(target) if self._is_type_like(target) else ""
        self.sink.add("dependency", [local or static], node, "call", member)

    @staticmethod
    def _is_type_like(node):
        """A name chain starting upper-case (`Util`, `pkg.Util`): a static call target."""
        if node.type == "identifier":
            return text(node)[:1].isupper()
        if node.type == "field_access":
            return text(node.child_by_field_name("field"))[:1].isupper() or \
                BodyReader._is_type_like(node.child_by_field_name("object"))
        return False

    def _method_reference(self, node):
        head = node.children[0]
        if self._is_type_like(head):
            self.sink.add("dependency", [dotted_name(head)], node, "call", text(node.children[-1]))
        elif head.type == "generic_type" or head.type == "type_identifier":
            self.sink.add("dependency", type_names(head), node, "call", text(node.children[-1]))

    def _cast_expression(self, node):
        self.sink.add("dependency", type_names(node.child_by_field_name("type")), node, "cast")

    def _instanceof_expression(self, node):
        right = node.child_by_field_name("right")
        self.sink.add("dependency", type_names(right), node, "local")

    def _catch_type(self, node):
        self.sink.add("dependency", type_names(node), node, "catch")

    def _class_literal(self, node):
        self.sink.add("dependency", type_names(first_child_of_type(node, "type_identifier",
                                                                   "generic_type", "scoped_type_identifier")),
                      node, "class")


def read_signature(sink, node, body_reader):
    """Type parameters, return/parameter/throws types of a method-like node, then its body."""
    sink = sink.with_generics(type_parameter_names(node))
    body_reader.sink = sink
    sink.add_annotations(node)
    sink.add("dependency", type_parameter_bounds(node), node, "generic")
    sink.add("dependency", type_names(node.child_by_field_name("type")), node, "return")
    parameters = node.child_by_field_name("parameters")
    for parameter in children_of_type(parameters, "formal_parameter", "spread_parameter") if parameters else []:
        type_node = parameter.child_by_field_name("type") or \
            first_child_of_type(parameter, "type_identifier", "generic_type", "array_type",
                                "scoped_type_identifier")
        names = type_names(type_node)
        sink.add("dependency", names, parameter, "param")
        sink.add_annotations(parameter)
        name_node = parameter.child_by_field_name("name") or first_child_of_type(
            first_child_of_type(parameter, "variable_declarator") or parameter, "identifier")
        if names and name_node is not None:
            body_reader.locals[text(name_node)] = names[0]
    for thrown in children_of_type(node, "throws"):
        sink.add("dependency", type_names(thrown), thrown, "throws")
    for child in node.children:
        if child.type in ("block", "constructor_body"):
            body_reader.visit(child)


# ---- types ------------------------------------------------------------------------------

class TypeReader:
    """Reads one type declaration (and its nested types) into declarations and refs."""

    def __init__(self, result, node, package, parent_id, static_calls):
        self.result = result
        self.node = node
        self.package = package
        self.static_calls = static_calls
        name = text(node.child_by_field_name("name"))
        self.id = child_id(parent_id or package, name) if (parent_id or package) else name
        self.sink = Sink(self.id, result.refs, type_parameter_names(node))
        self.body = node.child_by_field_name("body")
        self.fields = {}  # held member name -> type names
        self.created = self._created_members()
        self.decl = Declaration(
            id=self.id, name=name, kind=self._kind(parent_id), line=line(node), end_line=end_line(node),
            flags=self._flags(), namespace=package)

    def _kind(self, parent_id):
        kind = TYPE_KINDS[self.node.type]
        return "inner" if parent_id and kind == "class" else kind

    def _flags(self):
        found = modifier_words(self.node)
        return [flag for flag in ("abstract", "static") if flag in found]

    def _created_members(self):
        """Names of fields assigned `new T()` anywhere in this type (not nested types)."""
        names = set()
        if self.body is None:
            return names
        for found in descendants(self.body, stop=tuple(TYPE_KINDS)):
            if found.type == "assignment_expression" and is_creation(found.child_by_field_name("right")):
                names.add(BodyReader._member_name(found.child_by_field_name("left")))
        return names

    def read(self):
        self.result.declarations.append(self.decl)
        self._read_bases()
        self.sink.add_annotations(self.node)
        self.sink.add("dependency", type_parameter_bounds(self.node), self.node, "generic")
        self._read_record_components()
        if self.body is not None:
            members = self._body_members()
            self._index_fields(members)
            for member in members:
                self._read_member(member)
        return self.decl

    def _body_members(self):
        """Members of the body; enum constants and the declarations after them included."""
        members = []
        for child in self.body.children:
            if child.type == "enum_body_declarations":
                members += child.children
            else:
                members.append(child)
        return members

    def _read_bases(self):
        """extends / implements / interface extends: each listed type is a base, its generic
        arguments are plain dependencies."""
        for clause in self.node.children:
            if clause.type not in ("superclass", "super_interfaces", "extends_interfaces"):
                continue
            for listed in clause.named_children:
                for base in (listed.named_children if listed.type == "type_list" else [listed]):
                    names = type_names(base)
                    self.decl.base = self.decl.base or (names[0] if names else "")
                    self.sink.add("generalization", names[:1], base)
                    self.sink.add("dependency", names[1:], base, "generic")

    def _read_record_components(self):
        parameters = self.node.child_by_field_name("parameters")
        if self.node.type != "record_declaration" or parameters is None:
            return
        for parameter in children_of_type(parameters, "formal_parameter"):
            names = type_names(parameter.child_by_field_name("type"))
            name = text(parameter.child_by_field_name("name"))
            self.fields[name] = names
            self.sink.in_member(FIELD_MEMBER).add("association", names, parameter, "field")
            self.decl.members.append(Member(name, "property", text(
                parameter.child_by_field_name("type")), line(parameter)))

    def _index_fields(self, members):
        """Record each field name with its type, so calls through it are found."""
        for member in members:
            if member.type in FIELD_NODES:
                names = type_names(member.child_by_field_name("type"))
                for declarator in children_of_type(member, "variable_declarator"):
                    self.fields[text(declarator.child_by_field_name("name"))] = names

    def _read_member(self, member):
        if member.type in TYPE_KINDS:
            TypeReader(self.result, member, self.package, self.id, self.static_calls).read()
        elif member.type in FIELD_NODES:
            self._read_field(member)
        elif member.type in METHOD_NODES:
            self._read_method(member)
        elif member.type in ("static_initializer", "block"):
            self._body_reader(INIT_MEMBER).visit(member)
        elif member.type == "enum_constant":
            self._read_enum_constant(member)

    def _holding_kind(self, name, initializer):
        return "composition" if is_creation(initializer) or name in self.created else "association"

    def _read_field(self, node):
        type_node = node.child_by_field_name("type")
        self.sink.in_member(FIELD_MEMBER).add_annotations(node)
        for declarator in children_of_type(node, "variable_declarator"):
            name = text(declarator.child_by_field_name("name"))
            initializer = declarator.child_by_field_name("value")
            self.decl.members.append(Member(name, "field", text(type_node), line(node)))
            self.sink.in_member(FIELD_MEMBER).add(self._holding_kind(name, initializer),
                                                  type_names(type_node), node, "field")
            if initializer is not None:
                self._read_initializer(initializer)

    def _read_initializer(self, initializer):
        """Read calls inside an initializer; a plain `new T()` is already the composition."""
        if is_creation(initializer):
            for argument in initializer.children:
                if argument.type in ("argument_list", "class_body"):
                    self._body_reader(INIT_MEMBER).visit(argument)
        else:
            self._body_reader(INIT_MEMBER).visit(initializer)

    def _read_enum_constant(self, node):
        name = text(node.child_by_field_name("name"))
        self.decl.members.append(Member(name, "const", "", line(node)))
        self._body_reader(INIT_MEMBER).visit(node)

    def _body_reader(self, member):
        return BodyReader(self.sink.in_member(member), self.fields, self.static_calls)

    def _read_method(self, node):
        name = text(node.child_by_field_name("name")) or self.decl.name
        returns = node.child_by_field_name("type")
        self.decl.members.append(Member(name, "method", text(returns), line(node), end_line(node)))
        if name == "main" and {"static"} <= modifier_words(node):
            add_unique(self.decl.flags, entry_flags("program-main"))
        reader = self._body_reader(name)
        read_signature(reader.sink, node, reader)


# ---- file -------------------------------------------------------------------------------

@register
class JavaModule(LanguageModule):
    name = "java"
    patterns = ("*.java",)

    def parse(self, ctx):
        tree = PARSER.parse(ctx.read_bytes())
        result = FileResult()
        package = ""
        static_calls = {}
        for node in tree.root_node.children:
            if node.type == "package_declaration":
                package = dotted_name(first_child_of_type(node, "scoped_identifier", "identifier"))
            elif node.type == "import_declaration":
                self._read_import(result, node, static_calls)
            elif node.type in TYPE_KINDS:
                TypeReader(result, node, package, "", static_calls).read()
        if result.declarations:
            result.declarations[0].file_node = True
        return result

    @staticmethod
    def _read_import(result, node, static_calls):
        name_node = first_child_of_type(node, "scoped_identifier", "identifier")
        if name_node is None:
            return
        name = dotted_name(name_node)
        on_demand = first_child_of_type(node, "asterisk") is not None
        is_static = any(c.type == "static" for c in node.children)
        if on_demand:
            result.imports.append(name)
        elif is_static:
            owner, _, member = name.rpartition(".")
            result.aliases.setdefault(owner.rpartition(".")[2], Target(name=owner))
            static_calls[member] = owner
        else:
            result.aliases[name.rpartition(".")[2]] = Target(name=name)
