"""Code fingerprints for stale-note marks (docs/DESIGN.md 10): the one place that makes them.

A fingerprint is a short hash of the code a note describes, ignoring whitespace:
- class (`node:A`): its source lines (its own span, else the whole file for modules/scripts);
- function (`node:A#f`): its lines, from its first line to the next member (or the class end);
- connection (`edge:A→B`): the source lines where the connection is made (its `at` locations
  and call sites), so moving code never changes it but editing those lines does.
They are computed at scan time (each source file read once) and stored in graph.json, so
checking a mark later needs no source read.
"""

import hashlib
from collections import defaultdict
from pathlib import Path

from .model import FUNCTION_KINDS
from .notes import edge_key, function_key, node_key

FINGERPRINT_LENGTH = 10  # hex digits kept: plenty to notice a change, cheap to store


class SourceLines:
    """Lines of repo files, each file read once; a missing file has no lines."""

    def __init__(self, root):
        self.root = Path(root)
        self._files = {}

    def of(self, rel_path):
        if rel_path not in self._files:
            try:
                text = (self.root / rel_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            self._files[rel_path] = text.splitlines()
        return self._files[rel_path]

    def span(self, rel_path, first, last=None):
        """Lines first..last (1-based, last exclusive; None = to the end)."""
        return self.of(rel_path)[max(first - 1, 0):None if last is None else max(last - 1, 0)]


def digest(lines):
    """Short hash of the lines with all whitespace removed; None when there is no code."""
    text = "".join("".join(line.split()) for line in lines)
    if not text:
        return None
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def class_end(node):
    """First line after the class's own span (None: to the end of its file)."""
    return node.line + node.lines if node.span else None


def function_spans(node):
    """name -> [(first, end)] line ranges: from a function's line up to the next member."""
    starts = sorted({m.line for m in node.members if m.line > 0})
    spans = defaultdict(list)
    for member in node.members:
        if member.kind in FUNCTION_KINDS and member.line > 0:
            later = [s for s in starts if s > member.line]
            spans[member.name].append((member.line, later[0] if later else class_end(node)))
    return spans


def edge_lines(source, edges):
    """Text of the distinct lines where the pair's connections are made."""
    places = {(loc.file, loc.line) for e in edges
              for loc in [*e.at, *(a for c in e.calls for a in c.at)]}
    return [line for file, number in sorted(places) for line in source.span(file, number, number + 1)]


def compute_fingerprints(root, graph):
    """key -> fingerprint for every class, function and connection with readable code."""
    source = SourceLines(root)
    found = {}
    for node_id, node in graph.nodes.items():
        if node.kind == "external":
            continue
        found[node_key(node_id)] = digest(source.span(node.file, node.line, class_end(node)))
        for name, spans in function_spans(node).items():
            lines = [l for first, end in spans for l in source.span(node.file, first, end)]
            found[function_key(node_id, name)] = digest(lines)
    pairs = defaultdict(list)
    for edge in graph.edges.values():
        pairs[(edge.from_id, edge.to)].append(edge)
    for (from_id, to), edges in pairs.items():
        found[edge_key(from_id, to)] = digest(edge_lines(source, edges))
    return {key: fp for key, fp in sorted(found.items()) if fp}
