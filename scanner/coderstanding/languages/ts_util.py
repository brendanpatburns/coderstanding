"""Small tree-sitter helpers shared by the tree-sitter based language readers."""

from tree_sitter import Language, Parser


def make_parser(language_pointer):
    """A Parser for a tree-sitter grammar package's `language()` pointer."""
    return Parser(Language(language_pointer))


def text(node):
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def line(node):
    return node.start_point[0] + 1


def end_line(node):
    return node.end_point[0] + 1


def children_of_type(node, *types):
    return [child for child in node.children if child.type in types]


def first_child_of_type(node, *types):
    found = children_of_type(node, *types)
    return found[0] if found else None


def descendants(node, stop=()):
    """Every node below `node`, depth first. Nodes of a `stop` type are yielded, not entered.

    Iterative, so very deep trees (long C expressions) do not hit the recursion limit."""
    stack = node.children[::-1]
    while stack:
        child = stack.pop()
        yield child
        if child.type not in stop:
            stack.extend(child.children[::-1])
