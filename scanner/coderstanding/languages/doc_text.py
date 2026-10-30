"""Header comments as descriptions: the shared part of every reader's doc extraction.

Each reader finds its comment nodes (tree-sitter `comment` siblings, or the docstring) and
hands the raw text here. Everything is cut down the same way: the first paragraph, one
line, at most DOC_MAX characters (the doc is only a default description).
"""

import re

from .ts_util import text

DOC_MAX = 600
LINE_MARKER = re.compile(r"^\s*(?:///|//!|//|##|#)\s?")
BLOCK_STAR = re.compile(r"^\s*\*\s?")
TAG_LINE = re.compile(r"^\s*[@\\]\w+")
BRIEF = re.compile(r"^\s*[@\\]brief\s*")
LICENSE = re.compile(r"licen[sc]e|copyright|spdx|all rights reserved|permission is hereby|"
                     r"warranty|-\*-", re.IGNORECASE)
COMMENT_TYPES = ("comment", "line_comment", "block_comment")


def comment_lines(raw):
    """The text lines of one comment (`#`, `//`, `///`, `##` lines or a `/* */` block)."""
    raw = raw.strip()
    if raw.startswith("/*"):
        body = raw[2:-2] if raw.endswith("*/") else raw[2:]
        return [BLOCK_STAR.sub("", line).strip() for line in body.lstrip("*").splitlines()]
    return [LINE_MARKER.sub("", line).rstrip() for line in raw.splitlines()]


def trim_doc(lines):
    """First paragraph of the lines on one line, cut at DOC_MAX; doc tags (@param) end it."""
    lines = [BRIEF.sub("", line) for line in lines]
    kept = []
    for line in lines:
        if TAG_LINE.match(line):
            break
        kept.append(line)
    paragraphs = "\n".join(kept).strip().split("\n\n")
    paragraph = " ".join(paragraphs[0].split())
    if len(paragraph) > DOC_MAX:
        paragraph = paragraph[:DOC_MAX - 1].rstrip() + "…"
    return paragraph


def comments_text(raw_comments):
    return trim_doc([line for raw in raw_comments for line in comment_lines(raw)])


def is_doc_comment(raw, doc_markers):
    return raw.lstrip().startswith(doc_markers)


def comments_above(node):
    """Raw texts of the comments directly above a node (no blank line between), in order."""
    found, wanted_row = [], node.start_point[0]
    prev = node.prev_sibling
    while prev is not None and prev.type in COMMENT_TYPES and prev.end_point[0] + 1 >= wanted_row:
        found.insert(0, text(prev))
        wanted_row, prev = prev.start_point[0], prev.prev_sibling
    return found


def doc_above(node, doc_markers):
    """Doc comment (starting with one of doc_markers) directly above a type, trimmed."""
    raw = comments_above(node)
    return comments_text(raw) if raw and all(is_doc_comment(r, doc_markers) for r in raw) else ""


def leading_runs(root, doc_markers, skip_types=(), owner_types=()):
    """Comment runs at the top of a file: lists of raw texts, doc and plain comments in
    separate runs. Nodes of skip_types (`@tool`, `extends`) are passed over; any other node
    ends the header, and takes the run right above it when it is an owner_types node."""
    runs, current, row, break_row = [], [], None, None
    for node in root.children:
        if node.type in COMMENT_TYPES:
            raw = text(node)
            fresh = row is None or node.start_point[0] > row + 1 or \
                is_doc_comment(raw, doc_markers) != is_doc_comment(current[-1], doc_markers)
            if fresh:
                current = []
                runs.append(current)
            current.append(raw)
            row = node.end_point[0]
        elif node.type in skip_types:
            current = []
            row = None
        else:
            if node.type in owner_types and runs and row is not None and node.start_point[0] <= row + 1:
                runs.pop()
            break
    return [run for run in runs if run]


def file_doc(root, doc_markers, skip_types=(), owner_types=()):
    """The file's own description: its first doc-comment run at the top, else the first
    plain comment run that is not a shebang or a license header."""
    runs = leading_runs(root, doc_markers, skip_types, owner_types)
    for run in runs:
        if is_doc_comment(run[0], doc_markers):
            return comments_text(run)
    for run in runs:
        body = [line for raw in run if not raw.startswith("#!") for line in comment_lines(raw)]
        if body and not LICENSE.search("\n".join(body)):
            return trim_doc(body)
    return ""
