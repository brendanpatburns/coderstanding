"""JavaScript / TypeScript reader (docs/DESIGN.md 4.4b): a module node per file."""

import posixpath

import tree_sitter_javascript
import tree_sitter_typescript

from coderstanding.language import (FIELD_MEMBER, INIT_MEMBER, Declaration, FileResult,
                                    LanguageModule, Ref, Target, register)
from coderstanding import tsconfig
from coderstanding.model import Member, child_id, entry_flags

from . import doc_text
from .ts_util import children_of_type, descendants, first_child_of_type, end_line, line, make_parser, text

TS_PARSER = make_parser(tree_sitter_typescript.language_typescript())
PARSERS = {".ts": TS_PARSER, ".mts": TS_PARSER, ".cts": TS_PARSER,
           ".tsx": make_parser(tree_sitter_typescript.language_tsx())}
JS_PARSER = make_parser(tree_sitter_javascript.language())
SKIPPED_TYPES = {"Array", "Promise", "Record", "Map", "Set", "Partial", "Readonly", "Omit",
                 "Pick", "Function", "Object", "String", "Number", "Boolean", "Date",
                 "Error", "RegExp", "Required"}
EVENT_METHODS = ("addEventListener", "on", "once", "addListener")
CLASS_TYPES = ("class_declaration", "abstract_class_declaration", "interface_declaration")
DECLARATION_TYPES = CLASS_TYPES + ("enum_declaration",)
DOC_MARKERS = ("/**",)
STOP_AT_CLASSES = ("class_declaration", "abstract_class_declaration", "class")
STOP_AT_TYPES = STOP_AT_CLASSES + ("interface_declaration", "enum_declaration")
TYPE_NAME_NODES = ("type_identifier", "nested_type_identifier")
FIELD_NODES = ("public_field_definition", "field_definition", "property_signature")
PARAM_PROPERTY_MODIFIERS = ("accessibility_modifier", "readonly", "override_modifier")
INJECT_CALLS = ("inject",)  # `private api = inject(ApiService)` holds an ApiService
DEFAULT_EXPORT = "default"  # the member name `import X from './x'` asks for


def string_value(node):
    return text(first_child_of_type(node, "string_fragment")) if node is not None else ""


FUNCTION_NODES = ("method_definition", "function_declaration", "generator_function_declaration",
                  "arrow_function", "function_expression", "function")
NAMING_PARENTS = {"variable_declarator": "name", "public_field_definition": "name",
                  "field_definition": "property", "pair": "key"}


def function_name(function):
    """Name a function node goes by: its own, or the variable / field / property holding it."""
    own = function.child_by_field_name("name")
    if own is not None:
        return text(own)
    holder = function.parent
    field_name = NAMING_PARENTS.get(holder.type) if holder is not None else None
    return text(holder.child_by_field_name(field_name)) if field_name else ""


def enclosing_member(node):
    """Name of the function around `node` (anonymous callbacks count as their parent)."""
    while node is not None:
        if node.type in FUNCTION_NODES and function_name(node):
            return function_name(node)
        node = node.parent
    return INIT_MEMBER


def member_chain(node):
    """`a.b.c` / `this.a.b` -> ["a", "b", "c"] / ["this", "a", "b"]; [] if not a plain chain."""
    parts = []
    while node.type == "member_expression":
        parts.insert(0, text(node.child_by_field_name("property")))
        node = node.child_by_field_name("object")
    return [text(node)] + parts if node.type in ("identifier", "this") else []


class JavaScriptFile:
    def __init__(self, ctx):
        self.ctx = ctx
        self.module_id = ctx.rel_path
        self.result = FileResult()
        self.imported = set()
        self.local_classes = {}
        self.local_types = set()  # type aliases and type parameters: no node, no ref
        self.typed = set()  # ids of type nodes a specific reader already turned into refs

    def read(self):
        parser = PARSERS.get(posixpath.splitext(self.ctx.rel_path)[1], JS_PARSER)
        root = parser.parse(self.ctx.read_bytes()).root_node
        self.module = Declaration(self.module_id, posixpath.basename(self.module_id), "module",
                                  1, namespace=posixpath.dirname(self.module_id), file_node=True)
        self.module.doc = doc_text.file_doc(root, DOC_MARKERS, ("hash_bang_line",))
        if self.ctx.read_bytes().startswith(b"#!"):  # run directly: a script
            self.module.flags.extend(entry_flags("script-main"))
        self.result.declarations.append(self.module)
        self.find_classes(root)
        for node in root.children:
            self.read_top_level(node)
        self.read_calls(root, self.module_id, {}, "")
        self.read_type_uses(root, self.module_id)
        return self.result

    # --- top level --------------------------------------------------------------------

    def find_classes(self, root):
        for node in descendants(root):
            if node.type in DECLARATION_TYPES:
                name = text(node.child_by_field_name("name"))
                self.local_classes.setdefault(name, child_id(self.module_id, name))
            elif node.type in ("type_alias_declaration", "type_parameter"):
                self.local_types.add(text(node.child_by_field_name("name")))

    def read_top_level(self, node):
        outer = node  # comments sit above the `export`, not above the class inside it
        if node.type == "export_statement":
            self.read_export(node)
            node = node.child_by_field_name("declaration") or node.child_by_field_name("value") or node
            self.export_module_names(node)
        if node.type == "import_statement":
            self.read_import(node)
        elif node.type in CLASS_TYPES:
            self.read_class(node, outer)
        elif node.type == "class" and outer is not node:  # `export default class { }`
            self.read_class(node, outer, DEFAULT_EXPORT)
        elif node.type == "enum_declaration":
            self.read_enum(node, outer)
        elif node.type == "function_declaration":
            self.module.members.append(Member(text(node.child_by_field_name("name")),
                                              "function", line=line(node),
                                              end=end_line(node)))
            self.read_signature(node, self.module_id)
        elif node.type in ("lexical_declaration", "variable_declaration"):
            for declarator in children_of_type(node, "variable_declarator"):
                self.read_declarator(declarator)

    def read_declarator(self, declarator):
        name, value = declarator.child_by_field_name("name"), declarator.child_by_field_name("value")
        if value is None:
            return
        if value.type == "call_expression" and text(value.child_by_field_name("function")) == "require":
            self.read_require(name, value, declarator)
        elif value.type in ("arrow_function", "function_expression") and name.type == "identifier":
            self.module.members.append(Member(text(name), "function", line=line(declarator),
                                              end=end_line(declarator)))
            self.read_signature(value, self.module_id)

    # --- imports ----------------------------------------------------------------------

    @staticmethod
    def import_target(spec, member=""):
        return Target(path=spec, member=member)

    def add_import(self, spec, node, member=""):
        """An import of a file; with `member`, of one name in it (a class it imports is used)."""
        self.result.refs.append(Ref(self.module_id, self.import_target(spec, member),
                                    "dependency", line(node), "import"))

    def bind(self, name, target):
        self.imported.add(name)
        self.result.aliases[name] = target

    def read_import(self, node):
        spec = string_value(node.child_by_field_name("source"))
        self.add_import(spec, node)
        clause = first_child_of_type(node, "import_clause")
        for part in (clause.children if clause is not None else []):
            if part.type == "identifier":
                self.bind_import(text(part), spec, DEFAULT_EXPORT, node)
            elif part.type == "namespace_import":
                self.bind(text(first_child_of_type(part, "identifier")), self.import_target(spec))
            elif part.type == "named_imports":
                for item in children_of_type(part, "import_specifier"):
                    real = item.child_by_field_name("name")
                    alias = item.child_by_field_name("alias") or real
                    self.bind_import(text(alias), spec, text(real), node)

    def bind_import(self, local, spec, member, node):
        self.bind(local, self.import_target(spec, member))
        self.add_import(spec, node, member)

    # --- exports ----------------------------------------------------------------------

    def read_export(self, node):
        """Re-exports and the default export: names the resolver follows (barrel files)."""
        source = node.child_by_field_name("source")
        spec = string_value(source) if source is not None else ""
        if spec:
            self.add_import(spec, node)
        clause = first_child_of_type(node, "export_clause")
        namespace = first_child_of_type(node, "namespace_export")
        if namespace is not None and spec:  # export * as ns from './x'
            self.result.exports[text(first_child_of_type(namespace, "identifier"))] = Target(path=spec)
        elif clause is not None:  # export { A, B as C } [from './y']
            for item in children_of_type(clause, "export_specifier"):
                self.read_export_specifier(item, spec)
        elif spec:  # export * from './x'
            self.result.star_exports.append(spec)
        elif first_child_of_type(node, "default") is not None:
            self.read_default_export(node)

    def export_module_names(self, declaration):
        """`export function f`, `export const x`, `export type T`: names that are no node of
        their own mean this module (so a barrel's `export *` leads to the file declaring them)."""
        names = []
        if declaration.type in ("function_declaration", "type_alias_declaration",
                                "generator_function_declaration"):
            names = [declaration.child_by_field_name("name")]
        elif declaration.type in ("lexical_declaration", "variable_declaration"):
            names = [d.child_by_field_name("name") for d in
                     children_of_type(declaration, "variable_declarator")]
        for name in names:
            if name is not None and name.type in ("identifier", "type_identifier"):
                self.result.exports.setdefault(text(name), Target(id=self.module_id))

    def read_export_specifier(self, item, spec):
        name = text(item.child_by_field_name("name"))
        exported = text(item.child_by_field_name("alias")) or name
        if spec:
            self.result.exports[exported] = Target(path=spec, member=name)
        elif exported != name or name not in self.local_classes:
            self.result.exports[exported] = self.target_for(name)

    def read_default_export(self, node):
        declaration = node.child_by_field_name("declaration")
        value = node.child_by_field_name("value")
        if declaration is not None and declaration.type in DECLARATION_TYPES:
            name = text(declaration.child_by_field_name("name"))
            self.result.exports[DEFAULT_EXPORT] = Target(id=child_id(self.module_id, name))
        elif value is not None and member_chain(value):
            self.result.exports[DEFAULT_EXPORT] = self.target_for(".".join(member_chain(value)))

    def read_require(self, name, call, node):
        spec = string_value(first_child_of_type(call.child_by_field_name("arguments"), "string"))
        self.add_import(spec, node)
        if name.type == "identifier":
            self.bind(text(name), self.import_target(spec))
        elif name.type == "object_pattern":
            for item in name.named_children:
                self.bind_destructured(item, spec)

    def bind_destructured(self, item, spec):
        if item.type == "shorthand_property_identifier_pattern":
            self.bind(text(item), self.import_target(spec, text(item)))
        elif item.type == "pair_pattern":
            key, value = item.child_by_field_name("key"), item.child_by_field_name("value")
            self.bind(text(value), self.import_target(spec, text(key)))

    # --- classes ----------------------------------------------------------------------

    def read_class(self, node, outer, id_name=""):
        """id_name: the id part for an unnamed class (named after its file)."""
        name = text(node.child_by_field_name("name"))
        class_id = child_id(self.module_id, id_name or name)
        name = name or posixpath.splitext(posixpath.basename(self.module_id))[0]
        kind = "interface" if node.type == "interface_declaration" else "class"
        declaration = Declaration(class_id, name, kind, line(node), end_line=end_line(node),
                                  namespace=self.module_id,
                                  doc=doc_text.doc_above(outer, DOC_MARKERS),
                                  flags=["abstract"] if node.type == "abstract_class_declaration" else [])
        self.result.declarations.append(declaration)
        for base in self.base_names(node):
            declaration.base = declaration.base or base
            self.add_type_ref(class_id, base, "generalization", node, "")
        self.read_decorators(node, outer, class_id)
        body = node.child_by_field_name("body")
        fields = self.read_fields(body, class_id, declaration)
        for member in body.named_children:
            if member.type in ("method_definition", "method_signature"):
                declaration.members.append(Member(text(member.child_by_field_name("name")),
                                                  "method", line=line(member),
                                                  end=end_line(member)))
                self.read_signature(member, class_id)
        self.read_calls(body, class_id, fields, class_id)
        self.read_type_uses(node, class_id)

    def read_enum(self, node, outer):
        name = text(node.child_by_field_name("name"))
        self.result.declarations.append(Declaration(
            child_id(self.module_id, name), name, "enum", line(node), end_line=end_line(node),
            namespace=self.module_id, doc=doc_text.doc_above(outer, DOC_MARKERS)))

    def base_names(self, node):
        """Names after `extends` / `implements` (TS clauses, or the JS expression); type
        arguments (`Repo<User>`) are left for read_type_uses."""
        names = []
        for heritage in node.children:
            if heritage.type == "extends_type_clause":
                names += [self.base_name(n) for n in heritage.named_children]
            elif heritage.type == "class_heritage":
                names += self.heritage_names(heritage)
        return [n for n in names if n]

    def heritage_names(self, heritage):
        names = []
        for clause in heritage.named_children:
            if clause.type in ("extends_clause", "implements_clause"):
                names += [self.base_name(n) for n in clause.named_children
                          if n.type != "type_arguments"]
        if not names:  # plain JS: `extends Base`
            names += [".".join(member_chain(n)) for n in heritage.named_children
                      if member_chain(n)]
        return names

    def base_name(self, node):
        """`Base`, `ns.Base`, `Base<T>` -> the base's name; marks its type nodes as read."""
        if node.type == "generic_type":
            node = node.child_by_field_name("name")
        self.typed.update(n.id for n in [node, *descendants(node)])
        return ".".join(member_chain(node)) or text(node)

    def read_decorators(self, node, outer, class_id):
        """`@Component({imports: [X]})`: the decorator and the known names in its arguments
        (Angular / Nest modules list the classes they use there)."""
        decorators = children_of_type(node, "decorator")
        if outer is not node:
            decorators += children_of_type(outer, "decorator")
        for decorator in decorators:
            for part in [decorator, *descendants(decorator)]:
                chain = self.value_chain(part)
                if chain and self.is_known(chain[0]):
                    self.add_type_ref(class_id, ".".join(chain), "dependency", part, "decorator")

    @staticmethod
    def value_chain(node):
        """A name used as a value in a decorator: `X`, `ns.X` (not a part of a longer chain)."""
        if node.type not in ("identifier", "member_expression"):
            return []
        parent = node.parent
        if parent is not None and parent.type == "member_expression" and \
                parent.child_by_field_name("object") == node:
            return []
        if node.type == "member_expression" and parent is not None and \
                parent.type == "call_expression":
            return member_chain(node)[:-1]  # `Ns.decorate(...)` uses Ns
        return member_chain(node)

    # --- fields -----------------------------------------------------------------------

    def read_fields(self, body, class_id, declaration):
        """field name -> type name, emitting composition / association refs."""
        fields = {}
        for node in descendants(body, STOP_AT_CLASSES):
            if node.type in FIELD_NODES:
                self.read_declared_field(node, fields, class_id, declaration)
            elif node.type == "required_parameter" and \
                    first_child_of_type(node, *PARAM_PROPERTY_MODIFIERS):  # constructor(private a: A)
                self.read_declared_field(node, fields, class_id, declaration)
            elif node.type == "assignment_expression":
                self.read_assigned_field(node, fields, class_id, declaration)
        return fields

    def read_declared_field(self, node, fields, class_id, declaration):
        name_node = node.child_by_field_name("name") or node.child_by_field_name("property") \
            or node.child_by_field_name("pattern")
        names = self.type_names(node.child_by_field_name("type"))
        value = node.child_by_field_name("value")
        if names:
            self.set_field(fields, class_id, declaration, text(name_node), names[0],
                           "association", node)
            for name in names[1:]:  # Map<Key, User>: one ref per type name
                self.add_type_ref(class_id, name, "association", node, "field",
                                  from_member=FIELD_MEMBER)
        elif value is not None and value.type == "new_expression":
            self.set_new_field(fields, class_id, declaration, text(name_node), value)
        elif value is not None and self.injected_type(value):
            self.set_field(fields, class_id, declaration, text(name_node),
                           self.injected_type(value), "association", node)

    @staticmethod
    def injected_type(value):
        """`inject(ApiService)` -> "ApiService" (Angular's field injection), else ""."""
        if value.type != "call_expression":
            return ""
        chain = member_chain(value.child_by_field_name("function"))
        args = value.child_by_field_name("arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        if chain[-1:] and chain[-1] in INJECT_CALLS and first is not None:
            return ".".join(member_chain(first))
        return ""

    def read_assigned_field(self, node, fields, class_id, declaration):
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        chain = member_chain(left)
        if len(chain) != 2 or chain[0] != "this":
            return
        if right.type == "new_expression":
            self.set_new_field(fields, class_id, declaration, chain[1], right)
        elif right.type == "identifier":
            hinted = self.param_type(node, text(right))
            if hinted:
                self.set_field(fields, class_id, declaration, chain[1], hinted,
                               "association", node)

    def set_new_field(self, fields, class_id, declaration, field_name, new_node):
        chain = member_chain(new_node.child_by_field_name("constructor"))
        if chain:
            self.set_field(fields, class_id, declaration, field_name, ".".join(chain),
                           "composition", new_node)

    def set_field(self, fields, class_id, declaration, field_name, type_name, kind, node):
        if field_name in fields:
            return
        fields[field_name] = type_name
        declaration.members.append(Member(field_name, "field", type_name, line(node)))
        self.add_type_ref(class_id, type_name, kind, node, "field", from_member=FIELD_MEMBER)

    def param_type(self, node, param_name):
        while node is not None and node.type not in ("method_definition", "function_declaration"):
            node = node.parent
        if node is None:
            return ""
        for param in node.child_by_field_name("parameters").named_children:
            pattern = param.child_by_field_name("pattern")
            if pattern is not None and text(pattern) == param_name:
                names = self.type_names(param.child_by_field_name("type"))
                return names[0] if names else ""
        return ""

    # --- types and refs ---------------------------------------------------------------

    def target_for(self, name):
        head, _, rest = name.partition(".")
        if head in self.local_classes:
            return Target(id=self.local_classes[head], member=rest)
        return Target(name=name)

    def add_type_ref(self, from_id, name, kind, node, detail, call="", auto="",
                     from_member=None):
        if name in SKIPPED_TYPES or name in self.local_types:
            return
        self.result.refs.append(Ref(from_id, self.target_for(name), kind, line(node),
                                    detail, call, auto=auto, from_member=from_member))

    def type_names(self, annotation):
        if annotation is None:
            return []
        names, nested = [], set()
        for node in [annotation, *descendants(annotation)]:
            if node.id in nested:
                continue
            if node.type == "nested_type_identifier":
                nested.update(n.id for n in descendants(node))
                names.append(text(node))
            elif node.type == "type_identifier":
                names.append(text(node))
            self.typed.add(node.id)
        return [n for n in names if n not in SKIPPED_TYPES and n not in self.local_types]

    def read_type_uses(self, scope, owner_id):
        """Type names no specific reader took (`Repo<User>` arguments, `as User`, local
        variables, type aliases, interface bodies): a dependency when the name is known."""
        nested = set()
        for node in descendants(scope, STOP_AT_TYPES):
            if node.id in self.typed or node.id in nested or node.type not in TYPE_NAME_NODES:
                continue
            if node.type == "nested_type_identifier":
                nested.update(n.id for n in descendants(node))
            name = text(node)
            parent = node.parent
            if parent is not None and parent.child_by_field_name("name") == node and \
                    parent.type != "generic_type":
                continue  # the name a type, alias or type parameter is declared with
            if self.is_known(name.partition(".")[0]):
                self.add_type_ref(owner_id, name, "dependency", node, "type",
                                  from_member=enclosing_member(node))

    def read_signature(self, function, owner_id):
        member = function_name(function)
        parameters = function.child_by_field_name("parameters")
        for param in (parameters.named_children if parameters is not None else []):
            for name in self.type_names(param.child_by_field_name("type")):
                self.add_type_ref(owner_id, name, "dependency", param, "param",
                                  from_member=member)
        for name in self.type_names(function.child_by_field_name("return_type")):
            self.add_type_ref(owner_id, name, "dependency", function, "return",
                                  from_member=member)

    # --- calls, new, events -----------------------------------------------------------

    def read_calls(self, scope, owner_id, fields, class_id):
        stored = {n.id for n in descendants(scope, STOP_AT_CLASSES)
                  if n.type == "new_expression" and self.is_stored_new(n)}
        for node in descendants(scope, STOP_AT_CLASSES):
            if node.type == "call_expression":
                self.read_call(node, owner_id, fields, enclosing_member(node))
            elif node.type == "new_expression" and node.id not in stored:
                self.read_new(node, owner_id, enclosing_member(node))
            elif node.type in ("jsx_opening_element", "jsx_self_closing_element"):
                self.read_jsx(node, owner_id, enclosing_member(node))

    def read_jsx(self, node, owner_id, member):
        """`<UserCard/>`, `<Ns.Item/>`: the component is used (rendered) here."""
        chain = member_chain(node.child_by_field_name("name"))
        if chain and self.is_known(chain[0]):
            self.add_type_ref(owner_id, ".".join(chain), "dependency", node, "jsx", chain[-1],
                              from_member=member)

    @staticmethod
    def is_stored_new(node):
        parent = node.parent
        if parent.type == "assignment_expression":
            return text(parent.child_by_field_name("left")).startswith("this.")
        return parent.type in ("public_field_definition", "field_definition")

    def read_new(self, node, owner_id, member):
        chain = member_chain(node.child_by_field_name("constructor"))
        if chain and self.is_known(chain[0]):
            self.add_type_ref(owner_id, ".".join(chain), "dependency", node, "create", from_member=member)

    def is_known(self, name):
        return name in self.imported or name in self.local_classes

    def read_call(self, call, owner_id, fields, member):
        function = call.child_by_field_name("function")
        if function.type == "import":  # import('./page'): a lazy import
            arg = first_child_of_type(call.child_by_field_name("arguments"), "string")
            if arg is not None:
                self.add_import(string_value(arg), call)
            return
        chain = member_chain(function)
        if chain[:1] == ["this"]:
            self.read_this_call(chain, call, owner_id, fields, member)
        elif len(chain) == 1 and chain[0] in self.imported:
            self.add_type_ref(owner_id, chain[0], "dependency", call, "call", chain[0],
                               from_member=member)
        elif len(chain) >= 2 and self.is_known(chain[0]):
            if chain[-1] in EVENT_METHODS and self.read_event(call, owner_id, member, chain[:-1]):
                return
            self.add_type_ref(owner_id, ".".join(chain[:-1]), "dependency", call, "call",
                              chain[-1], from_member=member)

    def read_this_call(self, chain, call, owner_id, fields, member):
        if len(chain) < 3 or chain[1] not in fields:
            return
        if chain[-1] in EVENT_METHODS and len(chain) == 3 and self.read_event(
                call, owner_id, member, None, fields[chain[1]]):
            return
        self.add_type_ref(owner_id, fields[chain[1]], "association", call, "call", chain[2],
                           from_member=member)

    def read_event(self, call, owner_id, member, source_chain, source_type=None):
        """`source.on('name', handler)` -> signal ref to the source's type."""
        args = call.child_by_field_name("arguments").named_children
        if len(args) < 2 or args[0].type != "string":
            return False
        event = string_value(args[0])
        handler = ".".join(member_chain(args[1])) or "(inline)"
        source = source_type or ".".join(source_chain)
        self.add_type_ref(owner_id, source, "signal", call, event, auto=f"{event} -> {handler}",
                           from_member=member)
        return True


@register
class JavaScriptModule(LanguageModule):
    name = "javascript"
    patterns = ("*.js", "*.jsx", "*.mjs", "*.cjs", "*.ts", "*.tsx", "*.mts", "*.cts")
    path_suffixes = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts",
                     "/index.ts", "/index.tsx", "/index.js", "/index.jsx", "/index.mjs")

    def read_path_map(self, root, skip_dirs):
        return tsconfig.read_path_map(root, skip_dirs)

    def parse(self, ctx):
        return JavaScriptFile(ctx).read()
