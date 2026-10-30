"""Python reader (docs/DESIGN.md 4.4b): a module node per file, classes as its children."""

import ast
import re
from pathlib import Path

import tree_sitter_python

from coderstanding.language import (FIELD_MEMBER, INIT_MEMBER, Declaration, FileResult,
                                    LanguageModule, Ref, Target, register)
from coderstanding.model import Member, add_unique, child_id, entry_flags

from . import doc_text
from .ts_util import children_of_type, descendants, first_child_of_type, end_line, line, make_parser, text

PARSER = make_parser(tree_sitter_python.language())
INTERFACE_BASES = {"Protocol", "ABC"}
SKIPPED_TYPES = {"str", "int", "float", "bool", "bytes", "None", "list", "dict", "set",
                 "tuple", "object", "type", "Any", "self", "cls"}
STOP_AT_SCOPES = ("class_definition",)
DOTTED_NAME = re.compile(r"^[A-Za-z_][\w.]*$")


def module_id_for(ctx):
    """Dotted module path from the top of the package (or the file itself when not in one)."""
    parts = [] if ctx.path.stem == "__init__" else [ctx.path.stem]
    folder = ctx.path.parent
    while (folder / "__init__.py").is_file() and folder != ctx.root:
        parts.insert(0, folder.name)
        folder = folder.parent
    return ".".join(parts) or Path(ctx.rel_path).parent.name or ctx.path.stem


def package_of(ctx, module_id):
    """The package a relative import starts from."""
    return module_id if ctx.path.stem == "__init__" else module_id.rpartition(".")[0]


def docstring(body):
    """The docstring of a module or class body, trimmed; "" when there is none."""
    first = body.named_children[0] if body.named_children else None
    string = first.named_children[0] if first is not None and first.type == "expression_statement" \
        and first.named_children else None
    if string is None or string.type != "string":
        return ""
    try:
        value = ast.literal_eval(text(string))
    except (ValueError, SyntaxError):
        return ""
    return doc_text.trim_doc(value.splitlines()) if isinstance(value, str) else ""


def is_class_like(name):
    return name.rpartition(".")[2][:1].isupper()


def attribute_chain(node):
    """`a.b.c` -> ["a", "b", "c"]; [] when the chain does not start with a plain name."""
    parts = []
    while node.type == "attribute":
        parts.insert(0, text(node.child_by_field_name("attribute")))
        node = node.child_by_field_name("object")
    return [text(node)] + parts if node.type == "identifier" else []


class PythonFile:
    """Reads one file into a FileResult."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.module_id = module_id_for(ctx)
        self.result = FileResult()
        self.imported = set()  # file-local names bound by imports
        self.typing_names = set()
        self.local_classes = {}  # name -> id

    def read(self):
        root = PARSER.parse(self.ctx.read_bytes()).root_node
        module = Declaration(self.module_id, self.module_id.rpartition(".")[2], "module", 1,
                             namespace=self.module_id.rpartition(".")[0], file_node=True,
                             doc=docstring(root) or doc_text.file_doc(root, ()))
        if self.ctx.rel_path.rpartition("/")[2] == "__main__.py":  # `python -m package` starts it
            add_unique(module.flags, entry_flags("script-main"))
        self.result.declarations.append(module)
        self.find_classes(root, self.module_id)
        for node in descendants(root):  # imports anywhere: functions, try/except, TYPE_CHECKING
            if node.type in ("import_statement", "import_from_statement"):
                self.read_import(node)
        for statement in root.children:
            self.read_statement(statement, module)
        self.read_scope(root, self.module_id, {})
        return self.result

    # --- statements -------------------------------------------------------------------

    def read_statement(self, node, module):
        if node.type == "decorated_definition":
            node = node.child_by_field_name("definition")
        if node.type == "function_definition":
            module.members.append(Member(text(node.child_by_field_name("name")), "function",
                                         line=line(node), end=end_line(node)))
            self.read_signature(node, self.module_id)
        elif node.type == "class_definition":
            self.read_class(node, self.module_id)
        elif node.type == "if_statement" and "__main__" in text(node):
            add_unique(module.flags, entry_flags("script-main"))

    def find_classes(self, parent, parent_id):
        for node in descendants(parent, ("function_definition", "class_definition")):
            if node.type == "class_definition":
                name = text(node.child_by_field_name("name"))
                self.local_classes.setdefault(name, child_id(parent_id, name))

    # --- imports ----------------------------------------------------------------------

    def read_import(self, node):
        if node.type == "import_statement":
            for item in node.children_by_field_name("name"):
                self.import_module(item)
        else:
            self.import_from(node)

    def bind(self, name, target):
        self.imported.add(name)
        self.result.aliases[name] = target

    def import_module(self, item):
        alias = item.child_by_field_name("alias")
        name_node = item.child_by_field_name("name") if item.type == "aliased_import" else item
        module = text(name_node)
        target = Target(name=module)
        self.add_import_ref(target, item)
        if alias is not None:
            self.bind(text(alias), target)
        else:
            self.bind(module.partition(".")[0], Target(name=module.partition(".")[0]))
            self.bind(module, target)

    def import_from(self, node):
        module = self.absolute_module(node.child_by_field_name("module_name"))
        names = []
        for item in node.children_by_field_name("name"):
            real = item.child_by_field_name("name") if item.type == "aliased_import" else item
            alias = item.child_by_field_name("alias") if item.type == "aliased_import" else None
            names.append((text(real), text(alias) if alias is not None else text(real)))
        if module == "typing":
            self.typing_names.update(local for _, local in names)
        for real, local in names:
            target = Target(name=module, member=real) if module else Target(name=real)
            self.bind(local, target)
            self.add_import_ref(target, node)
        if not names:
            self.add_import_ref(Target(name=module), node)

    def absolute_module(self, node):
        """Dotted module a `from X import` names, with relative dots resolved."""
        if node.type != "relative_import":
            return text(node)
        dots = len(text(first_child_of_type(node, "import_prefix")))
        package = package_of(self.ctx, self.module_id).split(".") if package_of(
            self.ctx, self.module_id) else []
        package = package[:len(package) - (dots - 1)] if dots > 1 else package
        sub = first_child_of_type(node, "dotted_name")
        return ".".join(package + ([text(sub)] if sub is not None else []))

    def add_import_ref(self, target, node):
        self.result.refs.append(Ref(self.module_id, target, "dependency", line(node),
                                    "import"))

    # --- classes ----------------------------------------------------------------------

    def read_class(self, node, parent_id):
        name = text(node.child_by_field_name("name"))
        class_id = child_id(parent_id, name)
        bases = self.read_bases(node, class_id)
        kind = "interface" if INTERFACE_BASES & {b.rpartition(".")[2] for b in bases} else (
            "inner" if parent_id != self.module_id else "class")
        declaration = Declaration(class_id, name, kind, line(node), namespace=parent_id,
                                  end_line=end_line(node), base=bases[0] if bases else "",
                                  doc=docstring(node.child_by_field_name("body")))
        self.result.declarations.append(declaration)
        body = node.child_by_field_name("body")
        fields = self.read_fields(body, class_id, declaration)
        for item in body.children:
            item = item.child_by_field_name("definition") if item.type == "decorated_definition" else item
            if item.type == "function_definition":
                declaration.members.append(Member(text(item.child_by_field_name("name")),
                                                  "method", line=line(item),
                                                  end=end_line(item)))
                self.read_signature(item, class_id)
            elif item.type == "class_definition":
                self.read_class(item, class_id)
        self.read_scope(body, class_id, fields)

    def read_bases(self, node, class_id):
        arguments = node.child_by_field_name("superclasses")
        bases = []
        for base in (arguments.named_children if arguments is not None else []):
            if base.type in ("identifier", "attribute", "subscript"):
                chain = attribute_chain(base.child_by_field_name("value") or base)
                name = ".".join(chain)
                if name:
                    bases.append(name)
                    self.add_type_ref(class_id, name, "generalization", base, "")
        return bases

    def read_fields(self, body, class_id, declaration):
        """field name -> Target for the class's fields; emits composition/association refs."""
        fields = {}
        for assignment in descendants(body, ("class_definition",)):
            if assignment.type != "assignment":
                continue
            left = assignment.child_by_field_name("left")
            if left.type == "identifier" and assignment.parent.parent == body:
                self.add_field(fields, class_id, declaration, text(left), assignment)
            elif self.is_self_attribute(left):
                self.add_field(fields, class_id, declaration,
                               text(left.child_by_field_name("attribute")), assignment)
        return fields

    @staticmethod
    def is_self_attribute(node):
        return (node.type == "attribute" and text(node.child_by_field_name("object")) == "self")

    def add_field(self, fields, class_id, declaration, field_name, assignment):
        annotation = assignment.child_by_field_name("type")
        right = assignment.child_by_field_name("right")
        in_init = self.enclosing_function_name(assignment) == "__init__"
        type_names = self.type_names(annotation) if annotation is not None else []
        if type_names:
            self.set_field(fields, class_id, declaration, field_name, type_names[0],
                           "association", assignment)
        elif right is not None and right.type == "call" and in_init:
            chain = attribute_chain(right.child_by_field_name("function"))
            if chain and is_class_like(chain[-1]):
                self.set_field(fields, class_id, declaration, field_name, ".".join(chain),
                               "composition", assignment)
        elif right is not None and right.type == "identifier" and in_init:
            hinted = self.param_type(assignment, text(right))
            if hinted:
                self.set_field(fields, class_id, declaration, field_name, hinted,
                               "association", assignment)

    @staticmethod
    def enclosing_function_name(node):
        while node is not None and node.type != "function_definition":
            node = node.parent
        return text(node.child_by_field_name("name")) if node is not None else ""

    def param_type(self, node, param_name):
        while node.type != "function_definition":
            node = node.parent
        for param in node.child_by_field_name("parameters").named_children:
            if param.type in ("typed_parameter", "typed_default_parameter"):
                name_node = param.child_by_field_name("name") or param.children[0]
                if text(name_node) == param_name:
                    names = self.type_names(param.child_by_field_name("type"))
                    return names[0] if names else ""
        return ""

    def set_field(self, fields, class_id, declaration, field_name, type_name, kind, node):
        if field_name in fields:
            return
        fields[field_name] = type_name
        declaration.members.append(Member(field_name, "field", type_name, line(node)))
        self.add_type_ref(class_id, type_name, kind, node, "field", from_member=FIELD_MEMBER)

    # --- type names and refs ----------------------------------------------------------

    def target_for(self, name):
        head = name.partition(".")[0]
        if name in self.local_classes:
            return Target(id=self.local_classes[name])
        if head in self.local_classes:
            return Target(id=self.local_classes[head], member=name.partition(".")[2])
        return Target(name=name)

    def add_type_ref(self, from_id, name, kind, node, detail, call="", from_member=None):
        if name.rpartition(".")[2] in SKIPPED_TYPES or name in self.typing_names:
            return
        self.result.refs.append(Ref(from_id, self.target_for(name), kind, line(node),
                                    detail, call, from_member=from_member))

    def type_names(self, annotation):
        """Class names mentioned in an annotation, generics and unions unpacked."""
        names = []
        if annotation is None:
            return names
        skip_inside = set()
        for node in [annotation, *descendants(annotation)]:
            if node.id in skip_inside:
                continue
            if node.type == "attribute":
                skip_inside.update(n.id for n in descendants(node))
                names.append(text(node))
            elif node.type == "identifier":
                names.append(text(node))
            elif node.type == "string_content" and DOTTED_NAME.match(text(node)):
                names.append(text(node))
        return [n for n in names if n not in SKIPPED_TYPES and n not in self.typing_names]

    def read_signature(self, function, owner_id):
        member = text(function.child_by_field_name("name"))
        for param in function.child_by_field_name("parameters").named_children:
            if param.type in ("typed_parameter", "typed_default_parameter"):
                for name in self.type_names(param.child_by_field_name("type")):
                    self.add_type_ref(owner_id, name, "dependency", param, "param", from_member=member)
        for name in self.type_names(function.child_by_field_name("return_type")):
            self.add_type_ref(owner_id, name, "dependency", function, "return",
                                  from_member=member)

    # --- calls ------------------------------------------------------------------------

    def read_scope(self, scope, owner_id, fields):
        """Calls in this scope (not in nested classes) become refs from owner_id."""
        for call in descendants(scope, STOP_AT_SCOPES):
            if call.type == "call":
                self.read_call(call, owner_id, fields)

    def read_call(self, call, owner_id, fields):
        function = call.child_by_field_name("function")
        chain = attribute_chain(function)
        member = self.enclosing_function_name(call) or INIT_MEMBER
        if len(chain) >= 3 and chain[0] == "self" and chain[1] in fields:
            self.add_type_ref(owner_id, fields[chain[1]], "association", call, "call", chain[2],
                              member)
        elif len(chain) == 1:
            self.read_plain_call(chain[0], call, owner_id, member)
        elif len(chain) >= 2 and chain[0] != "self" and self.is_known(chain[0]):
            self.add_type_ref(owner_id, ".".join(chain[:-1]), "dependency", call, "call",
                              chain[-1], member)

    def is_known(self, name):
        return name in self.imported or name in self.local_classes

    def read_plain_call(self, name, call, owner_id, member):
        if name not in self.imported and name not in self.local_classes:
            return
        if is_class_like(name):
            self.add_type_ref(owner_id, name, "dependency", call, "create", from_member=member)
        else:
            self.add_type_ref(owner_id, name, "dependency", call, "call", name, member)


@register
class PythonModule(LanguageModule):
    name = "python"
    patterns = ("*.py",)

    def parse(self, ctx):
        return PythonFile(ctx).read()
