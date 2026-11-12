"""Stub functions (DESIGN 7f): the one rule every reader uses for "this function has no real
body yet", stored as `Member.stub`.

A stub is a function whose body, comments left out, is
- empty, `pass` or `...` (Python, GDScript), `:` (shell), and has no comment or a TODO / FIXME comment
  (a body with only another comment, `# nothing to do here`, is empty on purpose: not a stub); or
- one "not built" statement, comments or not: `raise NotImplementedError`, `throw new
  NotImplementedException / NotImplementedError / UnsupportedOperationException(...)`,
  Kotlin `TODO()`, GDScript `push_error("not implemented")`, or any `throw` / `raise` /
  `push_error` whose message says "not implemented".
A Python docstring does not count as a statement. A function with no body at all (abstract,
interface) is never a stub: readers pass None for it. Readers also skip constructors and
abstract methods (they are empty by design).
"""

import re

from .ts_util import descendants, text

COMMENT_TYPES = ("comment", "line_comment", "block_comment", "multiline_comment")
# A body made of one of these is unwrapped (Kotlin `function_body > block`, C# arrow bodies).
WRAPPER_TYPES = ("block", "statements", "function_body", "compound_statement", "statement_block",
                 "body", "arrow_expression_clause", "expression_statement")
EMPTY_STATEMENTS = ("pass", "...", ":", "")  # `:` = shell's do-nothing
TODO_COMMENT = re.compile(r"\b(todo|fixme|not\s+(yet\s+)?implemented|unimplemented)\b", re.I)
NOT_IMPLEMENTED_TYPES = ("NotImplementedError", "NotImplementedException",
                         "UnsupportedOperationException", "NotImplemented")
NOT_BUILT = re.compile(
    r"^(?:raise\s+(?:" + "|".join(NOT_IMPLEMENTED_TYPES) + r")\b.*"        # Python
    r"|throw\s+(?:new\s+)?(?:[\w.]*\.)?(?:" + "|".join(NOT_IMPLEMENTED_TYPES) + r")\b.*"
    r"|TODO\s*\(.*\)"                                                        # Kotlin
    r"|(?:throw|raise|push_error|printerr|assert|echo|die)\b.*not\s+(?:yet\s+)?implemented.*"
    r")$", re.I | re.S)


def is_docstring(node):
    """A Python docstring statement: an expression statement that is only a string."""
    return node.type == "expression_statement" and len(node.named_children) == 1 \
        and node.named_children[0].type in ("string", "concatenated_string")


def statements_of(body):
    """The body's statements (comments and docstrings left out), wrappers unwrapped."""
    found = [c for c in body.named_children if c.type not in COMMENT_TYPES and not is_docstring(c)]
    if len(found) == 1 and found[0].type in WRAPPER_TYPES and found[0].named_children:
        return statements_of(found[0])
    return found


def statement_text(node):
    """A statement as one plain line: spaces collapsed, no trailing `;`."""
    return " ".join(text(node).split()).rstrip(";").strip()


def is_stub(body, around=None):
    """True when `body` (the function's body node; None = no body) holds no real code yet.
    `around` (default: the body) is where its comments are looked for: Python and GDScript keep
    a comment before the first statement outside the body node, so readers pass the function."""
    if body is None:
        return False
    statements = statements_of(body)
    if len(statements) > 1:
        return False
    line = statement_text(statements[0]) if statements else ""
    if line and NOT_BUILT.match(line):
        return True
    if line not in EMPTY_STATEMENTS:
        return False
    comments = [text(n) for n in descendants(around or body) if n.type in COMMENT_TYPES]
    return not comments or any(TODO_COMMENT.search(c) for c in comments)
