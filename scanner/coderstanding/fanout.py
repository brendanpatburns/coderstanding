"""Function fan-out (docs/DESIGN.md 12c), applied by compute_view after folding.

An expanded (open) class shows its functions. Every visible line that touches an open class
is re-attached to functions: it starts at the calling function when its source is open and
ends at the called function when its target is open, else at the class. Lines with no
function behind them (field types, bases, call-less lines) stay on the class. Calls between
the same two endpoints merge into one link with a count. Derived lines fan out at their
visible ends using the calls on their first and last hop.
"""

from dataclasses import dataclass, field

from .model import (DATA_MEMBER_KINDS, FUNCTION_KINDS, called_function, calling_function,
                    function_names)

EXPAND_ALL = "*"  # expand every shown class
FIELD_KINDS = (*DATA_MEMBER_KINDS, "signal")  # listed under the functions


def functions_of(ctx, node_id):
    """The function rows of a class: its functions, then callers only seen in its calls."""
    calls = [c for link in ctx.outgoing.get(node_id, []) for c in link.calls]
    return function_names(ctx.graph.nodes[node_id], calls)


def class_parts(ctx, node_id):
    """{"functions": [{name, line}], "fields": [{name, kind, type}]} of one class."""
    node = ctx.graph.nodes[node_id]
    lines = {m.name: m.line for m in node.members if m.kind in FUNCTION_KINDS}
    rpcs = {m.name for m in node.members if m.rpc}
    return {"functions": [{"name": n, "line": lines.get(n, 0), **({"rpc": True} if n in rpcs else {})}
                          for n in functions_of(ctx, node_id)],
            "fields": [{"name": m.name, "kind": m.kind, "type": m.type}
                       for m in node.members if m.kind in FIELD_KINDS]}


@dataclass
class FanLink:
    """One drawn line between two endpoints (a class, or a function of an open class)."""
    from_id: str
    from_fn: str
    to: str
    to_fn: str
    direct: bool
    edge: str = None  # key of the class-level edge it came from (None: internal)
    kinds: set = field(default_factory=set)
    members: dict = field(default_factory=dict)  # called name -> count
    count: int = 0
    at: set = field(default_factory=set)
    as_: str = None
    faded: bool = False

    def add(self, kind, members=(), count=0, at=()):
        self.kinds.add(kind)
        for name, n in members:
            self.members[name] = self.members.get(name, 0) + n
        self.count += count
        self.at.update(at)

    def to_json(self):
        def end(node_id, fn):
            return f"{node_id}#{fn}" if fn else node_id
        return {"key": f"{end(self.from_id, self.from_fn)}→{end(self.to, self.to_fn)}",
                "edge": self.edge, "from": self.from_id, "from_fn": self.from_fn,
                "to": self.to, "to_fn": self.to_fn, "kinds": sorted(self.kinds),
                "direct": self.direct, "internal": self.from_id == self.to,
                "faded": self.faded, "as": self.as_, "count": self.count,
                "members": [{"name": n, "count": c} for n, c in
                            sorted(self.members.items(), key=lambda m: (-m[1], m[0]))],
                "at": [a.to_json() for a in sorted(self.at)]}


def call_kind(kind):
    """A call line drawn from a held field is a plain association (the diamond stays on the
    field's own line)."""
    return "association" if kind == "composition" else kind


class FanOut:
    """Collects the links of one view; `rows` = open class id -> its function names."""

    def __init__(self, rows):
        self.rows, self.links = rows, {}

    def source(self, node_id, from_member):
        fn = calling_function(from_member)
        return fn if fn in self.rows.get(node_id, ()) else None

    def target(self, node_id, member):
        fn = called_function(member)
        return fn if fn in self.rows.get(node_id, ()) else None

    def link(self, edge, from_fn, to_fn, direct, from_id=None, to=None):
        from_id, to = from_id or edge.from_id, to or edge.to
        key = (from_id, from_fn, to, to_fn, direct)
        if key not in self.links:
            self.links[key] = FanLink(from_id, from_fn, to, to_fn, direct,
                                      edge.key if edge else None)
            if edge:
                self.links[key].faded = edge.faded
                self.links[key].as_ = min((i for _, i in edge.as_from), default=None)
        return self.links[key]

    def add_direct(self, edge, link):
        if link.held:
            self.link(edge, None, None, True).add(link.kind)
        for call in link.calls:
            from_fn = self.source(edge.from_id, call.from_member)
            to_fn = self.target(edge.to, call.member)
            self.link(edge, from_fn, to_fn, True).add(
                call_kind(link.kind), [(call.member, call.count)], call.count, call.at)

    def add_derived(self, ctx, edge, path):
        """Fan out at the visible ends with the first and last hop's calls."""
        first = ctx.pair_calls.get((edge.from_id, path[0]), [])
        last = ctx.pair_calls.get((path[-1], edge.to), [])
        names = counted((c.member, c.count) for c in last)
        kind = call_kind(min(edge.kinds - {"composition"}, default="association"))
        sources = {}
        for call in first:
            fn = self.source(edge.from_id, call.from_member)
            if fn:
                sources.setdefault(fn, []).append(call)
        targets = {self.target(edge.to, n) for n in names}
        if sources:  # from each calling function; to one function only when it is the only one
            to_fn = next(iter(targets)) if len(names) == 1 else None
            for fn, calls in sources.items():
                self.link(edge, fn, to_fn, False).add(
                    kind, names.items(), sum(c.count for c in calls),
                    [a for c in calls for a in c.at])
        elif targets - {None}:
            for name, n in names.items():
                self.link(edge, None, self.target(edge.to, name), False).add(
                    kind, [(name, n)], n, [a for c in last if c.member == name for a in c.at])
        else:
            self.link(edge, None, None, False).add(kind, names.items(), sum(names.values()))

    def add_internal(self, link):
        """A line from a class to itself: shown only inside an open class."""
        for call in link.calls:
            from_fn = self.source(link.from_id, call.from_member)
            to_fn = self.target(link.to, call.member)
            self.link(None, from_fn, to_fn, True, link.from_id, link.to).add(
                call_kind(link.kind), [(call.member, call.count)], call.count, call.at)


def counted(pairs):
    found = {}
    for name, n in pairs:
        found[name] = found.get(name, 0) + n
    return found


def fanout_links(ctx, edges, open_ids):
    """JSON links for every edge touching an open class, plus calls inside open classes."""
    if not open_ids:
        return []
    fan = FanOut({i: set(functions_of(ctx, i)) for i in open_ids})
    for edge in edges:
        if edge.from_id not in open_ids and edge.to not in open_ids:
            continue
        for link in edge.direct_links:
            fan.add_direct(edge, link)
        for path in sorted(edge.paths):
            fan.add_derived(ctx, edge, path)
    for node_id in sorted(open_ids):
        for link in ctx.outgoing.get(node_id, []):
            if link.to == node_id:
                fan.add_internal(link)
    return [link.to_json() for link in sorted(fan.links.values(), key=link_order)]


def link_order(link):
    return (link.from_id, link.from_fn or "", link.to, link.to_fn or "", not link.direct)
