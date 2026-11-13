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


def lifted_children(node, named=False):
    """The children of `node`, with the children of any ERROR node (the grammar could not parse
    that part) put in its place, so declarations the grammar half-understood are still read.
    `named=True` keeps only named nodes (like `named_children`)."""
    found = []
    for child in node.children:
        if child.type == "ERROR":
            found.extend(lifted_children(child, named))
        elif child.is_named or not named:
            found.append(child)
    return found


def first_error_line(root, is_harmless=None):
    """Line of the first syntax error in a parsed file (an ERROR or missing node), 0 if none.
    `is_harmless(node)`: errors the grammar reports in valid code (a reader's known gaps)."""
    if not root.has_error:
        return 0
    stack = [root]
    while stack:
        node = stack.pop()
        if is_harmless and is_harmless(node):
            continue  # its whole subtree is the one known gap
        if node.type == "ERROR" or node.is_missing:
            return line(node)
        stack.extend(reversed([child for child in node.children if child.has_error or child.is_missing]))
    return 0
