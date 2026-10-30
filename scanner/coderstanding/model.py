"""Graph model: nodes (types), edges (UML relationships) and their merge rules."""

from bisect import bisect_left
from dataclasses import dataclass, field

NODE_KINDS = ("class", "interface", "struct", "record", "enum", "script", "inner",
              "module", "scene", "external")
# How an entry point starts (the one place that knows the entry kinds). Every one of them
# also carries the plain `entry` flag. App starts are level 1; script-main ones are tools.
APP_START_FLAGS = ("godot-main", "godot-main-scene", "autoload", "program-main")
TOOL_FLAGS = ("script-main",)
ENTRY_MECHANISMS = APP_START_FLAGS + TOOL_FLAGS
NODE_FLAGS = ("abstract", "static", "singleton", "partial", "entry", "test", "hub") + ENTRY_MECHANISMS
EDGE_KINDS = ("generalization", "realization", "composition", "association",
              "dependency", "signal")
HOLDING_KINDS = ("composition", "association")
STRUCT_KINDS = ("generalization", "realization")  # inheritance lines
FUNCTION_KINDS = ("method", "function")  # members that are functions (DESIGN 12c)
DATA_MEMBER_KINDS = ("field", "property", "event", "const")
INIT_MEMBER = "<init>"  # Call.from_member for initializers and top-level code
FIELD_MEMBER = "<field>"  # Call.from_member for a field's type reference
SIGNAL_ARROW = " → "  # a signal call is written `signal → handler`
EMIT_DETAIL = "emit"  # Ref.detail of `sig.emit()` / `EmitSignal(..)`; the call is the signal name
LAMBDA_HANDLER = "lambda"  # a signal handler written inline
VARIABLE_HANDLER = "<callable>"  # a handler held in a variable: its function is unknown
ANY_SIGNAL = "*"  # signal name of an emit / connect whose name is not a literal


def entry_flags(mechanism):
    """Flags for an entry point started by `mechanism` (one of ENTRY_MECHANISMS)."""
    return [mechanism, "entry"]


def child_id(parent_id, name):
    """Id of a type nested in another node (inner class, class in a module)."""
    return f"{parent_id}.{name}"


def add_unique(items, new_items):
    """Append the items not already in the list, keeping order."""
    for item in new_items:
        if item and item not in items:
            items.append(item)


@dataclass(frozen=True, order=True)
class Location:
    file: str
    line: int

    def to_json(self):
        return {"file": self.file, "line": self.line}

    @classmethod
    def from_json(cls, data):
        return cls(data["file"], data["line"])


def merge_locations(locations, new_locations):
    """Sorted union of two sorted, duplicate-free location lists (a new list).

    Inserts the (usually one or two) new locations by bisection: re-sorting the whole list
    on every merge was quadratic for types used thousands of times."""
    if len(new_locations) > len(locations):
        locations, new_locations = new_locations, locations
    merged = list(locations)
    for location in new_locations:
        index = bisect_left(merged, location)
        if index == len(merged) or merged[index] != location:
            merged.insert(index, location)
    return merged


@dataclass
class Member:
    name: str
    kind: str  # field, property, method, function, signal, const, ...
    type: str = ""  # type name as written
    line: int = 0
    end: int = 0  # last line of a function (0 = unknown)


@dataclass
class Call:
    member: str
    count: int = 0
    at: list = field(default_factory=list)
    from_member: str = None  # calling function in the from node; None when unknown

    def add(self, other):
        self.count += other.count
        self.at = merge_locations(self.at, other.at)


def calling_function(from_member):
    """The function a call sits in, or None when it has none (field types, bases)."""
    return None if from_member in (None, FIELD_MEMBER) else from_member


def called_function(member):
    """The function a call lands on: the member, or a signal connection's handler."""
    return member.rsplit(SIGNAL_ARROW, 1)[-1]


def function_names(node, calls):
    """A node's functions in member order, then callers only seen in its calls (`<init>`)."""
    names = [m.name for m in node.members if m.kind in FUNCTION_KINDS]
    add_unique(names, sorted({calling_function(c.from_member) for c in calls} - {None}))
    return names


@dataclass
class Edge:
    from_id: str
    to: str
    kind: str
    detail: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    at: list = field(default_factory=list)
    unsure: bool = False
    auto: str = ""

    @property
    def key(self):
        return (self.from_id, self.to, self.kind)

    def absorb(self, other):
        """Merge another edge of the same pair into this one."""
        add_unique(self.detail, other.detail)
        for call in other.calls:
            self._call(call.from_member, call.member).add(call)
        self.at = merge_locations(self.at, other.at)
        self.unsure = self.unsure and other.unsure
        self.auto = self.auto or other.auto

    def _call(self, from_member, member):
        for call in self.calls:
            if (call.from_member, call.member) == (from_member, member):
                return call
        call = Call(member, from_member=from_member)
        self.calls.append(call)
        return call


@dataclass
class Node:
    id: str
    name: str
    kind: str
    lang: str
    file: str
    line: int = 1
    flags: list = field(default_factory=list)
    base: str = ""
    namespace: str = ""
    level: int = 0
    members: list = field(default_factory=list)
    lines: int = 0  # size: the type's own line span, else (span False) its file's lines
    span: bool = False
    doc: str = ""  # the code's own header comment (short), a default description
    hub_degree: int = 0  # levels.py: neighbour count, set with the `hub` flag on promoted classes
    warnings: list = field(default_factory=list)  # checks.py: [{"kind": ..., ...}]
    external_refs: dict = field(default_factory=dict)  # (name, kind) -> Edge without a target node
    # The declaring files' imports (C# usings), so the stack reader can map outside names to
    # packages. Scan-time only: not written to graph.json.
    imports: list = field(default_factory=list, compare=False)

    def absorb(self, other):
        """Merge another declaration of the same id (partial class) into this node."""
        if (other.file, other.line) < (self.file, self.line):
            self.file, self.line = other.file, other.line
        add_unique(self.flags, other.flags)
        self.base = self.base or other.base
        self.doc = self.doc or other.doc
        self.namespace = self.namespace or other.namespace
        self.members.extend(other.members)
        self.lines += other.lines  # partial class: its parts add up
        self.span = self.span and other.span
        add_unique(self.imports, other.imports)
        for edge in other.external_refs.values():
            self.add_external(edge)

    def add_external(self, edge):
        merge_into(self.external_refs, edge)


def merge_into(edges, edge):
    """Add an edge to a key -> Edge dict, merging with an existing one of the same key."""
    existing = edges.get(edge.key)
    if existing:
        existing.absorb(edge)
    else:
        edges[edge.key] = edge


@dataclass
class Graph:
    repo: str = ""
    scanned_at: str = ""
    nodes: dict = field(default_factory=dict)
    edges: dict = field(default_factory=dict)
    stack: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)  # scan settings used (levels stage)
    hubs: dict = field(default_factory=dict)  # {"threshold": n, "ids": [...]} promoted to L1
    scanner_version: int = 0  # the scanner that wrote graph.json (0: built in memory)
    fingerprints: dict = field(default_factory=dict)  # note key -> code hash (fingerprints.py)

    def add_node(self, node):
        existing = self.nodes.get(node.id)
        if existing:
            existing.absorb(node)
        else:
            self.nodes[node.id] = node

    def add_edge(self, edge):
        merge_into(self.edges, edge)

    def fold_held_edges(self):
        """A pair with both composition and association is one held field: keep composition."""
        for (from_id, to, kind), edge in list(self.edges.items()):
            owned = self.edges.get((from_id, to, "composition"))
            if kind == "association" and owned:
                owned.absorb(edge)
                del self.edges[edge.key]
