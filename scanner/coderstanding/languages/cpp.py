"""C/C++ reader (docs/DESIGN.md 4.4c): a module node per header/source pair, types inside.

`foo.h` + `foo.cc` in one folder are one `module` node (id: repo path without extension).
Types declared in headers get namespace-qualified ids (`engine::geo::Mesh` ->
`engine.geo.Mesh`) so other files find them by name; types declared in a source file are
file-local (`<module>.Name`). Free functions declared in a header register as global aliases
(`ns.func` -> the header's module), so calls resolve to the module whose header declares
them. Only reads files; the shared resolver resolves names and builds edges.
"""

import posixpath
import re
from dataclasses import dataclass, field, replace

import tree_sitter_c
import tree_sitter_cpp

from coderstanding.language import FIELD_MEMBER, Declaration, FileResult, LanguageModule, Ref, Target, register
from coderstanding.model import Member, child_id, entry_flags

from . import doc_text
from .ts_util import children_of_type, descendants, end_line, line, make_parser, text

C_PARSER = make_parser(tree_sitter_c.language())
CPP_PARSER = make_parser(tree_sitter_cpp.language())

DOC_MARKERS = ("/**", "///")
HEADER_EXTS = (".h", ".hh", ".hpp")
SOURCE_EXTS = (".c", ".cc", ".cpp", ".cxx")
TYPE_KINDS = {"class_specifier": "class", "struct_specifier": "struct",
              "union_specifier": "struct", "enum_specifier": "enum"}
NAME_NODES = ("type_identifier", "namespace_identifier", "identifier", "field_identifier")
TEMPLATE_NODES = ("template_type", "template_function", "template_method")
INDIRECT = ("pointer_declarator", "reference_declarator", "abstract_pointer_declarator",
            "abstract_reference_declarator")
UNNAMED_INNER = ("reference_declarator", "abstract_reference_declarator",
                 "parenthesized_declarator")
TRANSPARENT = ("preproc_if", "preproc_ifdef", "preproc_else", "preproc_elif",
               "preproc_elifdef", "linkage_specification", "declaration_list")
OWNING_WRAPPERS = {"unique_ptr"}  # T inside is owned (composition)
SHARED_WRAPPERS = {"shared_ptr", "weak_ptr"}  # T inside is held, not owned (association)
CREATE_TEMPLATES = {"make_unique", "make_shared"}
CASTS = {"static_cast", "dynamic_cast", "reinterpret_cast", "const_cast"}
MACRO_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def join(scope, name):
    return f"{scope}.{name}" if scope and name else scope or name


# ---- parsing ----------------------------------------------------------------------------

def parse_tree(rel_path, source):
    """C grammar for `.c`; C++ for the rest. A `.h` falls back to C when only C parses it
    cleanly (C code using C++ keywords as names)."""
    ext = posixpath.splitext(rel_path)[1]
    if ext == ".c":
        return C_PARSER.parse(source)
    tree = CPP_PARSER.parse(source)
    if ext == ".h" and tree.root_node.has_error:
        c_tree = C_PARSER.parse(source)
        if not c_tree.root_node.has_error:
            return c_tree
    return tree


# ---- type names -------------------------------------------------------------------------

def split_type(node):
    """(dotted name, template argument nodes): `ns::Map<K, V>` -> ("ns.Map", [K, V])."""
    if node is None:
        return "", []
    if node.type == "qualified_identifier":
        scope, scope_args = split_type(node.child_by_field_name("scope"))
        name, name_args = split_type(node.child_by_field_name("name"))
        return join(scope, name), scope_args + name_args
    if node.type in TEMPLATE_NODES:
        name, _ = split_type(node.child_by_field_name("name"))
        arguments = node.child_by_field_name("arguments")
        return name, arguments.named_children if arguments is not None else []
    if node.type in NAME_NODES:
        return text(node), []
    if node.type == "nested_namespace_specifier":  # `namespace a::b {`
        return ".".join(split_type(c)[0] for c in node.named_children), []
    if node.type == "dependent_type":
        return split_type(node.named_children[0] if node.named_children else None)
    return "", []


def type_refs(node, held=True):
    """[(type name, held by value)] in a type: wrappers and template args unpacked.

    `std::unique_ptr<Foo>` -> [("Foo", held)]; `shared_ptr<Foo>` -> [("Foo", False)];
    `Vec<Foo *>` -> [("Vec", held), ("Foo", False)]. Built-in types give nothing."""
    if node is None:
        return []
    if node.type in TYPE_KINDS:  # `struct Foo *` (C) or an inline definition
        return type_refs(node.child_by_field_name("name"), held)
    if node.type == "type_descriptor":
        inner_held = held and not is_indirect(node.child_by_field_name("declarator"))
        return type_refs(node.child_by_field_name("type"), inner_held)
    name, arguments = split_type(node)
    if not name:
        return []
    short = name.rpartition(".")[2]
    if short in OWNING_WRAPPERS | SHARED_WRAPPERS:
        own, inner_held = [], held and short in OWNING_WRAPPERS
    else:
        own, inner_held = [(name, held)], held
    return own + [ref for arg in arguments for ref in type_refs(arg, inner_held)]


def type_names(node):
    return [name for name, _ in type_refs(node)]


# ---- declarators ------------------------------------------------------------------------

def declarator_chain(node):
    """The declarator and the ones nested in it, outermost first, down to the name."""
    while node is not None:
        yield node
        inner = node.child_by_field_name("declarator")
        if inner is None and node.type in UNNAMED_INNER and node.named_children:
            inner = node.named_children[-1]
        node = inner


def is_indirect(declarator):
    """True for `*x`, `&x`: the declared thing points at its type instead of holding it."""
    return any(node.type in INDIRECT for node in declarator_chain(declarator))


def function_declarator(declarator):
    """The function_declarator of a function (not of a function pointer), or None."""
    for node in declarator_chain(declarator):
        if node.type == "function_declarator":
            inner = node.child_by_field_name("declarator")
            return node if inner is not None and inner.type != "parenthesized_declarator" \
                else None
    return None


def declared_name(declarator):
    """The innermost node of a declarator: identifier, qualified_identifier, ..."""
    last = None
    for last in declarator_chain(declarator):
        pass
    return last


def short_name(node):
    """Plain name of a declared function or variable, "" for operators and the like."""
    name, _ = split_type(node)
    return name.rpartition(".")[2]


def template_parameters(node):
    params = node.child_by_field_name("parameters")
    names = set()
    for param in params.named_children if params is not None else []:
        found = [c for c in param.named_children if c.type == "type_identifier"]
        names.update(text(c) for c in found[:1])
    return names


def is_pure_virtual(field_decl):
    value = field_decl.child_by_field_name("default_value")
    return value is not None and text(value) == "0"


def parameters(function):
    """(type node, name) for each parameter of a function_declarator."""
    params = function.child_by_field_name("parameters")
    for param in params.named_children if params is not None else []:
        if param.type in ("parameter_declaration", "optional_parameter_declaration"):
            yield param.child_by_field_name("type"), short_name(
                declared_name(param.child_by_field_name("declarator")))


# ---- reference sink ---------------------------------------------------------------------

@dataclass
class Sink:
    """Collects Refs from one owner; `scope` is the dotted C++ scope used for lookup."""
    owner: str
    scope: str
    refs: list
    generics: set = field(default_factory=set)
    member: str = None  # enclosing function name (or FIELD_MEMBER) for Ref.from_member

    def in_member(self, name):
        return replace(self, member=name)

    def add(self, kind, names, node, detail="", call=""):
        for name in names:
            if name and name not in self.generics:
                self.refs.append(Ref(self.owner, Target(name=name), kind, line(node), detail,
                                     call, scope=self.scope, from_member=self.member))


@dataclass
class TypeInfo:
    """What bodies of a type's methods need: its held fields and its own method names."""
    fields: dict = field(default_factory=dict)  # field name -> primary type name
    methods: set = field(default_factory=set)


# ---- function bodies --------------------------------------------------------------------

class BodyReader:
    """Reads one function body: locals, calls, `new`, calls through fields and variables."""

    def __init__(self, reader, sink, info, variables):
        self.reader = reader  # the CppFile, for free calls
        self.sink = sink
        self.info = info
        self.variables = dict(variables)  # parameter / local name -> primary type name

    def read(self, *nodes):
        handlers = {"declaration": self._declaration, "new_expression": self._new_expression,
                    "call_expression": self._call_expression}
        for node in nodes:
            if node is None:
                continue
            for child in descendants(node):
                handler = handlers.get(child.type)
                if handler:
                    handler(child)

    def _declaration(self, node):
        type_node = node.child_by_field_name("type")
        names = type_names(type_node)
        self.sink.add("dependency", names, node, "local")
        for declarator in node.children_by_field_name("declarator"):
            name = short_name(declared_name(declarator))
            if name:
                self.variables[name] = names[0] if names else ""

    def _new_expression(self, node):
        if not self._stored_in_field(node):
            self.sink.add("dependency", type_names(node.child_by_field_name("type")), node,
                          "create")

    def _stored_in_field(self, node):
        """`field_ = new T`, `field_(new T)` or `field_ = make_unique<T>()`."""
        parent = node.parent
        if parent is not None and parent.type == "assignment_expression":
            return self._field_name(parent.child_by_field_name("left")) in self.info.fields
        if parent is not None and parent.type == "argument_list" and \
                parent.parent is not None and parent.parent.type == "field_initializer":
            return text(parent.parent.named_children[0]) in self.info.fields
        return False

    @staticmethod
    def _field_name(node):
        """`x` or `this->x` -> "x", otherwise ""."""
        if node is None:
            return ""
        if node.type in ("identifier", "field_identifier"):
            return text(node)
        if node.type == "field_expression" and \
                text(node.child_by_field_name("argument")) == "this":
            return text(node.child_by_field_name("field"))
        return ""

    def _call_expression(self, node):
        function = node.child_by_field_name("function")
        if function is None:
            return
        if function.type == "identifier":
            self._plain_call(node, text(function))
        elif function.type == "field_expression":
            self._member_call(node, function)
        elif function.type in ("qualified_identifier", "template_function"):
            self._named_call(node, function)

    def _plain_call(self, node, name):
        if MACRO_NAME.match(name) or name in self.variables or name in self.info.methods:
            return
        self.reader.add_free_call(self.sink, name, node)

    def _member_call(self, node, function):
        holder = function.child_by_field_name("argument")
        method = text(function.child_by_field_name("field"))
        held = self._field_name(holder)
        if held in self.info.fields and held not in self.variables:
            self.sink.add("association", [self.info.fields[held]], node, "call", method)
        elif holder.type == "identifier" and self.variables.get(text(holder)):
            self.sink.add("dependency", [self.variables[text(holder)]], node, "call", method)

    def _named_call(self, node, function):
        name, arguments = split_type(function)
        short = name.rpartition(".")[2]
        if short in CASTS or not short:
            return
        if short in CREATE_TEMPLATES:
            if not self._stored_in_field(node):
                created = [n for arg in arguments for n in type_names(arg)]
                self.sink.add("dependency", created[:1], node, "create")
        elif "." in name:
            self.sink.add("dependency", [name], node, "call", short)
        else:
            self._plain_call(node, name)


# ---- file -------------------------------------------------------------------------------

class CppFile:
    """Reads one C/C++ file into a FileResult."""

    def __init__(self, ctx):
        self.ctx = ctx
        stem, ext = posixpath.splitext(ctx.rel_path)
        self.module_id = stem
        self.is_header = ext in HEADER_EXTS
        self.result = FileResult()
        self.module = Declaration(stem, posixpath.basename(stem), "module", 1,
                                  namespace=posixpath.dirname(stem), file_node=True)
        # A source file with a header next to it leaves the function list to the header.
        self.lists_functions = self.is_header or not self._has_header()
        self.listed = set()  # function names already in the module's members
        self.local_functions = set()  # functions this file declares or defines
        self.local_types = {}  # qualified name -> id, for types declared in this file
        self.type_info = {}  # type id -> TypeInfo
        self.alias_targets = {}  # qualified alias name -> Target, for aliases of aliases
        self.macros = set()  # names #defined in this file
        self.free_calls = []  # (name, Ref), filtered against local functions/macros at the end
        self.anonymous = 0  # depth of `namespace {` blocks around the current node

    def _has_header(self):
        return any((self.ctx.root / f"{self.module_id}{ext}").is_file() for ext in HEADER_EXTS)

    def read(self):
        root = parse_tree(self.ctx.rel_path, self.ctx.read_bytes()).root_node
        self.module.doc = doc_text.file_doc(root, DOC_MARKERS, ("preproc_include", "preproc_def", "preproc_ifdef"))
        self.result.declarations.append(self.module)
        if self.is_header:
            self.add_include_names()
        self.read_scope(root.children, "", set())
        skipped = self.local_functions | self.macros
        self.result.refs += [ref for name, ref in self.free_calls if name not in skipped]
        return self.result

    # --- namespace level --------------------------------------------------------------

    def read_scope(self, nodes, namespace, generics):
        for node in nodes:
            kind = node.type
            if kind in TRANSPARENT:
                self.read_scope(node.children, namespace, generics)
            elif kind == "namespace_definition":
                self.read_namespace(node, namespace, generics)
            elif kind == "template_declaration":
                self.read_scope(node.children, namespace, generics | template_parameters(node))
            elif kind == "preproc_include":
                self.read_include(node)
            elif kind == "using_declaration":
                self.read_using(node, namespace)
            elif kind == "alias_declaration":
                self.add_alias(namespace, text(node.child_by_field_name("name")),
                               node.child_by_field_name("type"))
            elif kind in ("preproc_def", "preproc_function_def"):
                self.macros.add(text(node.child_by_field_name("name")))
            elif kind == "function_definition":
                self.read_function(node, namespace, generics)
            elif kind in ("declaration", "type_definition"):
                self.read_declaration(node, namespace, generics)
            elif kind in TYPE_KINDS:
                self.read_type(node, namespace, None, generics)

    def read_namespace(self, node, namespace, generics):
        name, _ = split_type(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        self.anonymous += not name
        self.read_scope(body.children if body else [], join(namespace, name), generics)
        self.anonymous -= not name

    def read_using(self, node, namespace):
        """`using namespace a;` is an import; `using a::Foo;` an alias for Foo."""
        written, _ = split_type(node.named_children[-1]) if node.named_children else ("", [])
        if any(c.type == "namespace" for c in node.children):
            self.result.imports.append(written)
        elif "." in written:
            self.add_alias_target(namespace, written.rpartition(".")[2], written)

    def add_alias(self, namespace, name, type_node):
        """`using Name = T<...>;` / `typedef T Name;`: Name means T (template args dropped)."""
        names = type_names(type_node)
        if names:
            self.add_alias_target(namespace, name, names[0])

    def add_alias_target(self, namespace, name, written):
        if not name or written == name:  # `typedef struct Foo Foo;`
            return
        local_id = self.local_types.get(join(namespace, written))
        target = self.alias_targets.get(join(namespace, written)) or (
            Target(id=local_id) if local_id else Target(name=written, scope=namespace))
        self.alias_targets[join(namespace, name)] = target  # later aliases may name this one
        if self.is_header:
            self.result.global_aliases[join(namespace, name)] = target
        else:
            self.result.aliases.setdefault(name, target)

    def add_include_names(self):
        """`#include "x.h"` / "dir/x.h" from anywhere (include paths) finds this header."""
        parts = self.ctx.rel_path.split("/")
        for start in range(len(parts)):
            self.result.global_aliases["/".join(parts[start:])] = Target(id=self.module_id)

    def read_include(self, node):
        path = node.child_by_field_name("path")
        if path is None:
            return
        written = text(path).strip('"<>')
        target = self.local_include(written) if path.type == "string_literal" \
            else Target(name=written)
        self.result.refs.append(Ref(self.module_id, target, "dependency", line(node), "import"))

    def local_include(self, written):
        """`#include "x.h"` next to this file or from the repo root, else by name."""
        beside = posixpath.join(posixpath.dirname(self.ctx.rel_path), written)
        for rel in dict.fromkeys(posixpath.normpath(p) for p in (beside, written)):
            if not rel.startswith("../") and self.ctx.file_exists(rel):
                return Target(path=rel)
        return Target(name=written)

    def read_declaration(self, node, namespace, generics):
        type_node = node.child_by_field_name("type")
        typedefs = [d for d in node.children_by_field_name("declarator")] \
            if node.type == "type_definition" else []
        if type_node is not None and type_node.type in TYPE_KINDS:
            typedef_name = short_name(declared_name(typedefs[0])) if typedefs else ""
            self.read_type(type_node, namespace, None, generics, typedef_name)
        for typedef in typedefs:
            self.add_alias(namespace, short_name(declared_name(typedef)), type_node)
        if typedefs:
            return
        sink = Sink(self.module_id, namespace, self.result.refs, generics)
        for declarator in node.children_by_field_name("declarator"):
            function = function_declarator(declarator)
            if function is not None:
                self.add_function(function, node, namespace, sink)
            else:
                self.add_held(sink, type_node, declarator, node)

    def add_function(self, function, node, namespace, sink):
        """A free function declared or defined here: module member, global alias, types."""
        name = short_name(function.child_by_field_name("declarator"))
        if name:
            self.local_functions.add(name)
            if self.lists_functions and name not in self.listed:
                self.listed.add(name)
                self.module.members.append(Member(name, "function", line=line(node), end=end_line(node)))
            if name == "main" and node.type == "function_definition":
                self.module.flags.extend(entry_flags("program-main"))
            if self.is_header:
                self.result.global_aliases[join(namespace, name)] = Target(id=self.module_id)
        self.read_signature(sink, node, function)

    def read_signature(self, sink, node, function):
        sink = sink.in_member(short_name(function.child_by_field_name("declarator")))
        sink.add("dependency", type_names(node.child_by_field_name("type")), node, "return")
        for type_node, _ in parameters(function):
            sink.add("dependency", type_names(type_node), node, "param")

    def add_held(self, sink, type_node, declarator, node, info=None, owner=None):
        """A field (or module-level variable): composition by value, association by pointer."""
        refs = type_refs(type_node, not is_indirect(declarator))
        for name, held in refs:
            sink.in_member(FIELD_MEMBER).add("composition" if held else "association", [name], node, "field")
        name = short_name(declared_name(declarator))
        primary = refs[0][0] if refs else ""
        if info is not None and name:
            info.fields[name] = primary
        if owner is not None and name:
            owner.members.append(Member(name, "field", primary, line(node)))

    # --- functions --------------------------------------------------------------------

    def read_function(self, node, namespace, generics, owner=None, info=None):
        """owner: (id, qualified name) of the type the function is a method of."""
        function = function_declarator(node.child_by_field_name("declarator"))
        if function is None:
            return
        name_node = function.child_by_field_name("declarator")
        if owner is None and name_node.type == "qualified_identifier":
            owner, info = self.out_of_line_owner(name_node, namespace)
        if owner:
            sink = Sink(owner[0], owner[1], self.result.refs, generics)
            self.read_signature(sink, node, function)
        else:
            sink = Sink(self.module_id, namespace, self.result.refs, generics)
            self.add_function(function, node, namespace, sink)
        sink = sink.in_member(short_name(name_node))
        variables = {}
        for type_node, param in parameters(function):
            names = type_names(type_node)
            if param:
                variables[param] = names[0] if names else ""
        BodyReader(self, sink, info or TypeInfo(), variables).read(
            node.child_by_field_name("body"), *children_of_type(node, "field_initializer_list"))

    def out_of_line_owner(self, name_node, namespace):
        """`Foo::bar` -> ((id, qualified name) of Foo, its TypeInfo if declared in this file).

        The id is a guess from the enclosing namespace; when no such node exists (the class
        lives elsewhere under another name) the resolver drops these refs."""
        qualified, _ = split_type(name_node.child_by_field_name("scope"))
        qualified = join(namespace, qualified)
        owner_id = self.local_types.get(qualified, qualified)
        return (owner_id, qualified), self.type_info.get(owner_id)

    def add_free_call(self, sink, name, node):
        ref = Ref(sink.owner, Target(name=name), "dependency", line(node), "call", name,
                  scope=sink.scope,
                  from_member=sink.member)
        self.free_calls.append((name, ref))

    # --- types ------------------------------------------------------------------------

    def read_type(self, node, namespace, parent, generics, typedef_name=""):
        """A class/struct/union/enum with a body. parent: (id, qualified name) of the outer type."""
        body = node.child_by_field_name("body")
        name_node = node.child_by_field_name("name")
        if body is None or (name_node is not None and name_node.type in TEMPLATE_NODES):
            return  # forward declaration / elaborated type use, or a template specialization
        name = split_type(name_node)[0] or typedef_name
        if not name:
            return
        qualified = join(parent[1] if parent else namespace, name)
        type_id = self.type_id(qualified, parent, name)
        kind = TYPE_KINDS[node.type]
        if parent and kind != "enum":
            kind = "inner"
        owner = node.parent if node.parent is not None and node.parent.type == "template_declaration" else node
        decl = Declaration(type_id, name, kind, line(node), namespace=namespace,
                           doc=doc_text.doc_above(owner, DOC_MARKERS))
        self.result.declarations.append(decl)
        if kind == "enum":
            return
        sink = Sink(type_id, qualified, self.result.refs, set(generics))
        self.read_bases(node, decl, sink)
        info = self.type_info.setdefault(type_id, TypeInfo())
        bodies = []
        pure = self.read_members(body.children, decl, sink, info, (type_id, qualified),
                                 namespace, generics, bodies)
        self.set_abstract(decl, pure, info)
        for function, function_generics in bodies:
            self.read_function(function, namespace, function_generics, (type_id, qualified),
                               info)

    def type_id(self, qualified, parent, name):
        """Header types: the qualified name. Source-file types: file-local, under the module."""
        if self.is_header:
            return qualified
        type_id = child_id(parent[0], name) if parent else child_id(self.module_id, qualified)
        self.local_types[qualified] = type_id
        self.result.aliases.setdefault(name, Target(id=type_id))
        if not self.anonymous:  # opaque types: `struct Foo;` in a header, the body in a .cc
            self.result.global_aliases.setdefault(qualified, Target(id=type_id))
        return type_id

    @staticmethod
    def read_bases(node, decl, sink):
        clause = next((c for c in node.children if c.type == "base_class_clause"), None)
        for base in clause.named_children if clause is not None else []:
            name, arguments = split_type(base)
            if not name:
                continue
            decl.base = decl.base or name
            sink.add("generalization", [name], base)
            sink.add("dependency", [n for arg in arguments for n in type_names(arg)], base)

    def read_members(self, nodes, decl, sink, info, parent, namespace, generics, bodies):
        """Fields, methods and nested types; returns [is pure virtual] for each method."""
        pure = []
        for node in nodes:
            kind = node.type
            if kind in TRANSPARENT:
                pure += self.read_members(node.children, decl, sink, info, parent, namespace,
                                          generics, bodies)
            elif kind == "template_declaration":
                inner = generics | template_parameters(node)
                pure += self.read_members(node.children, decl, sink, info, parent, namespace,
                                          inner, bodies)
            elif kind == "function_definition":
                self.add_method(node, decl, info, pure, False)
                bodies.append((node, generics))
            elif kind in ("field_declaration", "declaration"):
                self.read_field_declaration(node, decl, sink, info, parent, namespace,
                                            generics, pure)
            elif kind in TYPE_KINDS:
                self.read_type(node, namespace, parent, generics)
        return pure

    def read_field_declaration(self, node, decl, sink, info, parent, namespace, generics,
                               pure):
        type_node = node.child_by_field_name("type")
        if type_node is not None and type_node.type in TYPE_KINDS:
            self.read_type(type_node, namespace, parent, generics)
        for declarator in node.children_by_field_name("declarator"):
            function = function_declarator(declarator)
            if function is not None:
                self.add_method(node, decl, info, pure, is_pure_virtual(node))
                self.read_signature(sink, node, function)
            else:
                self.add_held(sink, type_node, declarator, node, info, decl)

    @staticmethod
    def add_method(node, decl, info, pure, is_pure):
        function = function_declarator(node.child_by_field_name("declarator"))
        name_node = function.child_by_field_name("declarator") if function else None
        if name_node is None or name_node.type == "destructor_name":
            return
        name = short_name(name_node)
        if not name or name == decl.name:  # constructors do not count
            return
        info.methods.add(name)
        decl.members.append(Member(name, "method", line=line(node), end=end_line(node)))
        pure.append(is_pure)

    @staticmethod
    def set_abstract(decl, pure, info):
        """Pure virtuals make a class abstract; only pure virtuals and no fields: interface."""
        if not any(pure):
            return
        if all(pure) and not info.fields and decl.kind in ("class", "struct"):
            decl.kind = "interface"
        else:
            decl.flags.append("abstract")


@register
class CppModule(LanguageModule):
    name = "cpp"
    patterns = tuple(f"*{ext}" for ext in SOURCE_EXTS + HEADER_EXTS)

    def parse(self, ctx):
        return CppFile(ctx).read()
