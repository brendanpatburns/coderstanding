"""The view engine (docs/DESIGN.md 5, 6, 6b, 7, 9): the only place views are computed.

`compute_view(graph, notes, ...)` returns a JSON-ready dict of visible nodes, faded outside
nodes and merged edges (direct or derived, with via paths and per-hop calls). The viewer and
the text CLI both draw this; neither computes views itself.
"""

from dataclasses import dataclass, field

from .checks import split_ignored, warning_text
from .fanout import EXPAND_ALL, FIELD_KINDS, class_parts, fanout_links
from .model import (FUNCTION_KINDS, INIT_MEMBER, STRUCT_KINDS, called_function, calling_function)
from .notes import (ARROW, FINGERPRINTED, FUNCTION_SEP, TEXT_FIELDS, Notes, domain_id, edge_key,
                    function_key, node_key)
from .testcode import covering_tests, index_tests, is_test, retest_files

ALL, CONCRETE = "all", "concrete"
CODE_AUTHOR = "code"  # `description_by` of a description taken from the code
OWNER_RANK = {"composition": 0, "association": 1, "dependency": 2}
DEFAULT_SHOW = {"scene": True, "external": True, "test": False}
ABSTRACT_MEMBER_KINDS = ("abstract", "signal")  # members that are not real code


@dataclass
class Link:
    """A graph edge after the view transform (concrete view rewrites interface targets)."""
    from_id: str
    to: str
    kind: str
    calls: list
    as_: str = ""  # the interface this link went through in the concrete view
    held: bool = False  # a field type, base or call-less line: stays on the class (DESIGN 12c)


@dataclass
class Merged:
    """All links between one visible pair A -> B."""
    from_id: str
    to: str
    kinds: set = field(default_factory=set)
    calls: dict = field(default_factory=dict)  # (from_member, member) -> count
    paths: set = field(default_factory=set)  # tuples of hidden ids on derived paths
    direct: bool = False
    as_from: set = field(default_factory=set)  # (link from, interface) pairs
    faded: bool = False
    direct_links: list = field(default_factory=list)  # the links drawn as-is (no via)

    @property
    def key(self):
        return f"{self.from_id}→{self.to}"

    def add(self, link, via):
        self.kinds.add(link.kind)
        for call in link.calls:
            key = (call.from_member, call.member)
            self.calls[key] = self.calls.get(key, 0) + call.count
        if via:
            self.paths.add(via)
        else:
            self.direct = True
            self.direct_links.append(link)
        if link.as_:
            self.as_from.add((link.from_id, link.as_))


def is_pure_abstract(node):
    """An abstract class with no real code: only abstract members (or none)."""
    return ("abstract" in node.flags
            and all(m.kind in ABSTRACT_MEMBER_KINDS for m in node.members))


def effective_level(graph, notes, node_id):
    """A level set in notes wins over the scan's guess."""
    return notes.value(node_key(node_id), "level", graph.nodes[node_id].level)


def is_interface_like(node):
    return node.kind == "interface"


class ViewContext:
    """Graph + notes + view options, with the lookups every view step shares."""

    def __init__(self, graph, notes, view=ALL, show=None):
        if view not in (ALL, CONCRETE):
            raise ValueError(f"view must be {ALL!r} or {CONCRETE!r}")
        self.graph, self.notes, self.view = graph, notes, view
        self.show = {**DEFAULT_SHOW, **(show or {})}
        self.removed = {i for i, n in graph.nodes.items() if self._removed(n)}
        self.allowed = [i for i in graph.nodes if i not in self.removed]
        self._impls = {}
        self.links = list(self._make_links())
        self.incoming, self.outgoing, self.pair_calls = {}, {}, {}
        for link in self.links:
            self.incoming.setdefault(link.to, []).append(link)
            self.outgoing.setdefault(link.from_id, []).append(link)
            self.pair_calls.setdefault((link.from_id, link.to), []).extend(link.calls)
        self._owners = {}
        self._tests = None

    def covering_tests(self, node_id):
        if self._tests is None:
            self._tests = index_tests(self.graph)
        return covering_tests(self.graph, node_id, self._tests)

    # --- node facts (notes override the scan) ---
    def level(self, node_id):
        return effective_level(self.graph, self.notes, node_id)

    def domain(self, node_id):
        return self.notes.value(node_key(node_id), "domain")

    def singleton(self, node_id):
        node = self.graph.nodes[node_id]
        return self.notes.value(node_key(node_id), "singleton", "singleton" in node.flags)

    def is_external(self, node_id):
        node = self.graph.nodes.get(node_id)
        return node is not None and node.kind == "external"

    def is_abstraction(self, node_id):
        """Interfaces, and in the concrete view pure abstract classes too."""
        node = self.graph.nodes[node_id]
        return is_interface_like(node) or (self.view == CONCRETE and is_pure_abstract(node))

    def _removed(self, node):
        """Switched-off types; hidden tests are removed outright, never folded (DESIGN 12a)."""
        if node.kind in ("scene", "external") and not self.show.get(node.kind, True):
            return True
        if is_test(node) and not self.show.get("test"):
            return True
        return self.view == CONCRETE and (is_interface_like(node) or is_pure_abstract(node))

    # --- interface bindings (DESIGN 9) ---
    def candidates(self, node_id):
        """Classes that implement or extend this abstraction in the repo."""
        kinds = ("realization", "generalization")
        return sorted({e.from_id for e in self.graph.edges.values()
                       if e.to == node_id and e.kind in kinds and e.from_id != node_id})

    def impls(self, node_id):
        """Bound implementations: notes bindings, else every candidate (auto when one)."""
        if node_id not in self._impls:
            bound = self.notes.value(node_key(node_id), "bindings")
            ids = [i for i in bound if i in self.graph.nodes] if bound else self.candidates(node_id)
            self._impls[node_id] = [i for i in ids if i not in self.removed]
        return self._impls[node_id]

    def needs_binding(self):
        found = []
        for node_id in sorted(self.graph.nodes):
            if not self.is_abstraction(node_id) or self.notes.has(node_key(node_id), "bindings"):
                continue
            options = self.candidates(node_id)
            if len(options) != 1:
                found.append({"id": node_id, "candidates": options})
        return found

    # --- links ---
    def _make_links(self):
        for edge in self.graph.edges.values():
            if edge.from_id in self.removed or edge.from_id not in self.graph.nodes:
                continue
            held = edge.kind in STRUCT_KINDS or not edge.calls or "field" in edge.detail
            if edge.to not in self.removed:
                if edge.to in self.graph.nodes:
                    yield Link(edge.from_id, edge.to, edge.kind, edge.calls, held=held)
            elif self.view == CONCRETE and edge.kind not in STRUCT_KINDS \
                    and self.is_abstraction(edge.to):
                for impl in self.impls(edge.to):
                    if impl != edge.from_id:
                        yield Link(edge.from_id, impl, edge.kind, edge.calls, edge.to, held)

    def owner(self, node_id):
        """Owner per DESIGN 5.1: composition, else association, else dependency source;
        ties go to the higher level (smaller number), then the id."""
        if node_id not in self._owners:
            options = [l for l in self.incoming.get(node_id, [])
                       if l.kind in OWNER_RANK and l.from_id != node_id]
            options.sort(key=lambda l: (OWNER_RANK[l.kind], self.level(l.from_id), l.from_id))
            self._owners[node_id] = options[0].from_id if options else None
        return self._owners[node_id]

    # --- visibility ---
    def visible_at(self, level):
        """Ids visible at a slider level. Level 0: domain cards, classes without a domain
        at level 1, and outside nodes."""
        shown = set()
        for node_id in self.allowed:
            if self.is_external(node_id):
                shown.add(node_id)
            elif level == 0:
                if self.domain(node_id):
                    shown.add(domain_id(self.domain(node_id)))
                elif self.level(node_id) <= 1:
                    shown.add(node_id)
            elif self.level(node_id) <= level:
                shown.add(node_id)
        return shown


class Folder:
    """Finds the visible stand-ins of hidden nodes (DESIGN 5 rules 1-3, 6b)."""

    def __init__(self, ctx, visible, fold_domains=False):
        self.ctx, self.visible, self.fold_domains = ctx, visible, fold_domains
        self._cache = {}

    def stand_ins(self, node_id, side):
        """[(visible id, chain of hidden ids from node_id outwards)], one per stand-in.
        side "src": the node is an edge's source; "dst": its target (may pass forward)."""
        key = (node_id, side)
        if key not in self._cache:
            found = {}
            for stand_in, chain in self._rep(node_id, side, set()):
                if stand_in not in found or len(chain) < len(found[stand_in]):
                    found[stand_in] = chain
            self._cache[key] = sorted(found.items())
        return self._cache[key]

    def _rep(self, node_id, side, seen):
        if node_id in self.visible:
            return [(node_id, ())]
        domain = self.fold_domains and node_id in self.ctx.graph.nodes and self.ctx.domain(node_id)
        if domain:
            return [(domain_id(domain), (node_id,))]
        if node_id in seen:
            return []
        seen.add(node_id)
        return [(stand_in, (node_id, *chain))
                for nxt in self._next(node_id, side)
                for stand_in, chain in self._rep(nxt, side, seen)]

    def _next(self, node_id, side):
        ctx = self.ctx
        if ctx.is_abstraction(node_id) and ctx.impls(node_id):
            return ctx.impls(node_id)
        owner = ctx.owner(node_id)
        if owner:
            return [owner]
        if side == "src":
            return []
        return [l.to for l in ctx.outgoing.get(node_id, []) if l.kind not in STRUCT_KINDS]


def collapse(ctx, folder):
    """Redraw every link between visible stand-ins; internal ones are dropped."""
    merged = {}

    def record(a, b, link, via):
        key = (a, b)
        if key not in merged:
            merged[key] = Merged(a, b)
        merged[key].add(link, via)

    for link in ctx.links:
        if link.kind in STRUCT_KINDS:
            if link.from_id in folder.visible and link.to in folder.visible:
                record(link.from_id, link.to, link, ())
            continue
        for a, a_chain in folder.stand_ins(link.from_id, "src"):
            for b, b_chain in folder.stand_ins(link.to, "dst"):
                if a != b:
                    record(a, b, link, tuple(reversed(a_chain)) + b_chain)
    return merged


@dataclass
class ViewResult:
    shown: set
    faded: set
    edges: dict
    folder: Folder
    level: int


def level_view(ctx, level):
    folder = Folder(ctx, ctx.visible_at(level), fold_domains=level == 0)
    return ViewResult(set(folder.visible), set(), collapse(ctx, folder), folder, level)


def edge_via(ctx, from_id, to, level):
    """The hidden ids the A -> B line runs through at this level."""
    edge = level_view(ctx, level).edges.get((from_id, to))
    return sorted({n for path in edge.paths for n in path}) if edge else []


def neighbours(ctx, node_id, level):
    """Stand-ins linked to node_id when it is visible at `level`."""
    folder = Folder(ctx, ctx.visible_at(level) | {node_id}, fold_domains=level == 0)
    return {e.to if e.from_id == node_id else e.from_id
            for e in collapse(ctx, folder).values() if node_id in (e.from_id, e.to)}


def zoom_view(ctx, zoom):
    """DESIGN 6: the zoom set at one more level of detail, plus faded links out of it."""
    level = zoom.get("level", 1)
    from_id, to = zoom["from"], zoom.get("to")
    if to:
        via = zoom.get("via")
        if via is None:
            via = edge_via(ctx, from_id, to, level)
        core = {from_id, to} | {n for n in via if n in ctx.graph.nodes
                                and n not in ctx.removed and ctx.level(n) <= level + 1}
    else:
        core = {from_id} | neighbours(ctx, from_id, level + 1)
    folder = Folder(ctx, ctx.visible_at(level) | core, fold_domains=level == 0)
    return focus_on(core, collapse(ctx, folder), folder, level + 1)


def focus_on(core, edges, folder, level):
    """Keep edges touching the core; fade the ones that leave it and their far ends."""
    kept = {k: e for k, e in edges.items() if e.from_id in core or e.to in core}
    for edge in kept.values():
        edge.faded = edge.faded or not (edge.from_id in core and edge.to in core)
    faded = {i for e in kept.values() if e.faded for i in (e.from_id, e.to)} - core
    return ViewResult(set(core), faded, kept, folder, level)


def stand_ins_of(result, cls):
    """The shown ids that stand for a class: itself, the ends of derived lines through it, or
    the visible nodes it folded into (DESIGN 7)."""
    if cls in result.shown or cls in result.faded:
        return {cls}
    through = [e for e in result.edges.values() if any(cls in p for p in e.paths)]
    if through:
        return {i for e in through for i in (e.from_id, e.to)}
    if cls not in result.folder.ctx.graph.nodes:
        return set()
    for side in ("src", "dst"):
        stand_ins = {s for s, _ in result.folder.stand_ins(cls, side)}
        if stand_ins:
            return stand_ins
    return set()


def split_focus(graph, focus):
    """(class ids, edge pairs, function ids) of focus targets given as view ids: a class `A`,
    a connection `A→B`, a function `A#f` (a function's class counts as a focus class)."""
    classes, pairs, functions = [], [], []
    for target in focus:
        if ARROW in target:
            pair = tuple(target.split(ARROW, 1))
            pairs.append(pair)
            classes += pair
            continue
        node_id, sep, name = target.rpartition(FUNCTION_SEP)
        if sep and node_id and target not in graph.nodes:
            functions.append(target)
            classes.append(node_id)
        else:
            classes.append(target)
    return classes, pairs, functions


def function_neighbours(result, functions):
    """(ids, line keys) a focused function `A#f` really touches: the other end of each direct
    line of A holding a call made by f, or a call to f."""
    near, keys = set(), set()
    for function in functions:
        cls, _, name = function.rpartition(FUNCTION_SEP)
        for key, edge in result.edges.items():
            made = edge.from_id == cls and any(
                calling_function(c.from_member) == name
                for link in edge.direct_links for c in link.calls)
            received = edge.to == cls and any(
                called_function(c.member) == name
                for link in edge.direct_links for c in link.calls)
            if made or received:
                near.add(edge.to if made else edge.from_id)
                keys.add(key)
    return near, keys


def target_focus(result, graph, focus):
    """DESIGN 7 / 7c: highlight the targets (or what they folded into), one faded layer out
    (zoom's rule: `focus_on`); the classes a focused function really calls or is called by are
    shown unfaded. Returns (view, focus json or None when no target is in the graph)."""
    classes, pairs, functions = split_focus(graph, focus)
    core = set().union(*(stand_ins_of(result, cls) for cls in classes)) if classes else set()
    if not core:
        return result, None
    edges = {(a, b) for x, y in pairs
             for a, _ in result.folder.stand_ins(x, "src")
             for b, _ in result.folder.stand_ins(y, "dst")} & set(result.edges)
    data = {"nodes": sorted(core), "edges": sorted(f"{a}{ARROW}{b}" for a, b in edges),
            "functions": sorted(f for f in functions if f.rpartition(FUNCTION_SEP)[0] in core)}
    focused = focus_on(core, result.edges, result.folder, result.level)
    near, keys = function_neighbours(focused, data["functions"])
    for key in keys:
        focused.edges[key].faded = False
    focused.shown |= near
    focused.faded -= near
    return focused, data


# --- JSON output ---

def note_json(notes, key, names=TEXT_FIELDS, graph=None):
    """The key's text fields (hint, description, status, tags, rules, review), each with its author
    as `<name>_by` (a name or None) and `<name>_ai` (True for an AI draft). With a graph, `stale`
    lists the fields whose code changed since they were written."""
    data = {}
    if graph:
        data["stale"] = notes.stale_fields(key, graph, [n for n in names if n in FINGERPRINTED])
    for name in names:
        data[name], by = notes.get(key, name)
        data[f"{name}_by"], data[f"{name}_ai"] = by, notes.is_ai(key, by)
    return data


def with_code_doc(data, doc):
    """Description shown: human > AI > the code's own header comment (`description_by` "code")."""
    if data["description"] is None and doc:
        data.update(description=doc, description_by=CODE_AUTHOR, description_ai=False)
    return data


def size_json(node):
    """Lines and members (with how many are functions and fields); `span` False means the
    lines are the whole file's."""
    kinds = [m.kind for m in node.members]
    return {"lines": node.lines, "members": len(kinds), "span": node.span,
            "functions": sum(k in FUNCTION_KINDS for k in kinds),
            "fields": sum(k in FIELD_KINDS for k in kinds)}


def warnings_json(warnings):
    return [{**w, "text": warning_text(w)} for w in warnings]


def node_json(ctx, node_id, faded):
    notes = ctx.notes
    if node_id not in ctx.graph.nodes:  # a domain card
        name = node_id.split(":", 1)[1]
        return {"id": node_id, "name": name, "kind": "domain", "level": 0, "flags": [],
                "file": "", "line": 0, "faded": faded, "domain": name, "dep": None,
                "lines": 0, "members": 0, "span": False, "functions": 0, "fields": 0,
                "warnings": [], "ignored_warnings": [], "ignore_warnings": [], "tested_by": [],
                **note_json(notes, node_key(node_id))}
    node = ctx.graph.nodes[node_id]
    flags = [f for f in node.flags if f != "singleton"]
    if ctx.singleton(node_id):
        flags.append("singleton")
    ignore = notes.ignored_warnings(node_id)
    shown, ignored = split_ignored(node, notes)
    return {"id": node_id, "name": node.name, "kind": node.kind, "level": ctx.level(node_id),
            "flags": flags, "file": node.file, "line": node.line, "faded": faded,
            "hub_degree": node.hub_degree, "domain": ctx.domain(node_id),
            "dep": node.namespace if node.kind == "external" else None,
            **size_json(node), "warnings": warnings_json(shown),
            "ignored_warnings": warnings_json(ignored), "ignore_warnings": ignore,
            "tested_by": ctx.covering_tests(node_id) if node.kind != "external" else [],
            **with_code_doc(note_json(notes, node_key(node_id), graph=ctx.graph), node.doc)}


def edge_hint(ctx, edge):
    """(hint, author, note key): the pair's own note, else the note on `from -> interface`."""
    keys = [edge_key(edge.from_id, edge.to)] + [edge_key(f, i) for f, i in sorted(edge.as_from)]
    for key in keys:
        hint, by = ctx.notes.get(key, "hint")
        if hint is not None:
            return hint, by, key
    return None, None, None


def hops_json(ctx, edge, path):
    ids = [edge.from_id, *path, edge.to]
    def calls(a, b):
        pairs = {(c.from_member, c.member) for c in ctx.pair_calls.get((a, b), [])}
        return [{"member": m, "from_member": f}
                for f, m in sorted(pairs, key=lambda c: (c[1], c[0] or ""))]
    return [{"from": a, "to": b, "calls": calls(a, b)} for a, b in zip(ids, ids[1:])]


def edge_layers(ctx, edge):
    """The architecture rules (shown, not ignored) this real line breaks: [{rule, why, edge, line}]."""
    node = ctx.graph.nodes.get(edge.from_id)
    if not node or not edge.direct:
        return []
    shown, _ = split_ignored(node, ctx.notes)
    return [{k: w[k] for k in ("rule", "why", "edge", "file", "line")}
            for w in shown if w["kind"] == "layer" and w["to"] == edge.to]


def edge_json(ctx, edge):
    hint, by, hint_key = edge_hint(ctx, edge)
    own = note_json(ctx.notes, edge_key(edge.from_id, edge.to), ("description", "review"),
                    ctx.graph)
    stale = own["stale"] + (ctx.notes.stale_fields(hint_key, ctx.graph, ("hint",))
                            if hint_key else [])
    paths = sorted(edge.paths, key=lambda p: (len(p), p))
    calls = sorted(edge.calls.items(), key=lambda c: (-c[1], c[0][1], c[0][0] or ""))
    return {"key": edge.key, "from": edge.from_id, "to": edge.to, "direct": edge.direct,
            "kinds": sorted(edge.kinds),
            "calls": [{"member": m, "from_member": f, "count": n} for (f, m), n in calls],
            "via": [list(p) for p in paths],
            "hops": [hops_json(ctx, edge, p) for p in paths],
            "as": min((i for _, i in edge.as_from), default=None),
            "hint": hint, "hint_by": by, "stale": stale,
            "description": own["description"], "description_by": own["description_by"],
            "review": own["review"], "review_by": own["review_by"], "review_ai": own["review_ai"],
            "layer": edge_layers(ctx, edge), "faded": edge.faded}


def sort_key(node):
    return (node["level"], node["name"], node["id"])


def compute_view(graph, notes=None, *, level=1, view=ALL, show=None, zoom=None,
                 focus=None, expand=(), badges=None):
    """The view for (level, view, type toggles, optional zoom, optional focus).

    zoom: {"from", "to" (None for a node zoom), "level", "via" (optional: found if missing)}.
    In a zoom the returned "level" is the level shown (zoom level + 1).
    expand: class ids to open into their functions (EXPAND_ALL = every shown class); only
    shown classes open. Adds "expanded" and "fanout" (fanout.py, DESIGN 12c).
    focus: targets as view ids (`A`, `A→B`, `A#f`) to highlight, as specs and tour steps do
    (DESIGN 7, 7c); a function target also opens its class. "focus" in the result lists the
    highlighted ids ({"nodes", "edges", "functions"}), None without a focus in the graph.
    badges: {view id (`A`, `A→B`): count} of cards linking it (cards.link_index); each shown
    class and line then gets "cards" = its count (DESIGN 7d).
    """
    ctx = ViewContext(graph, notes or Notes(), view, show)
    if zoom:
        zoom = dict(zoom)
        if zoom.get("to") and zoom.get("via") is None:
            zoom["via"] = edge_via(ctx, zoom["from"], zoom["to"], zoom.get("level", 1))
        result = zoom_view(ctx, zoom)
    else:
        result = level_view(ctx, level)
    focused = None
    if focus:
        result, focused = target_focus(result, graph, focus)
        if focused:
            expand = [*expand, *(f.rpartition(FUNCTION_SEP)[0] for f in focused["functions"])]
    nodes = [node_json(ctx, i, False) for i in result.shown]
    nodes += [node_json(ctx, i, True) for i in result.faded]
    edges = sorted(result.edges.items())
    open_ids = expanded_ids(ctx, result, expand)
    edge_rows = [{**edge_json(ctx, e), "fanned": e.from_id in open_ids or e.to in open_ids}
                 for _, e in edges]
    if badges is not None:
        for row in nodes:
            row["cards"] = badges.get(row["id"], 0)
        for row in edge_rows:
            row["cards"] = badges.get(row["key"], 0)
    return {"level": result.level, "view": view, "zoom": zoom,
            "counts": {"shown": len(result.shown), "total": len(ctx.allowed)},
            "nodes": sorted(nodes, key=sort_key),
            "edges": edge_rows,
            "needs_binding": ctx.needs_binding(),
            "focus": focused,
            "expanded": {i: expanded_json(ctx, i) for i in sorted(open_ids)},
            "fanout": fanout_links(ctx, [e for _, e in edges], open_ids)}


def expanded_json(ctx, node_id):
    """An open class: its functions (with their notes) and its fields/signals."""
    parts = class_parts(ctx, node_id)
    parts["functions"] = [{**f, **note_json(ctx.notes, function_key(node_id, f["name"]),
                                                    graph=ctx.graph)}
                          for f in parts["functions"]]
    return parts


def expanded_ids(ctx, result, expand):
    """The shown graph classes (not outside code or domain cards) to open into functions."""
    wanted = result.shown if EXPAND_ALL in expand else set(expand) & result.shown
    return {i for i in wanted if i in ctx.graph.nodes and not ctx.is_external(i)}


# --- reading helpers for the CLI: outline, find, impact (docs/DESIGN.md 11) ---

IMPACT_DEPTH = 3
Unit = tuple  # (class id, function name or None): what impact and brief look at


def node_span(node):
    """(first, last) line of a node: its own span, else its whole file; last 0 if unknown."""
    if node.span:
        return node.line, node.line + node.lines - 1
    return (1, node.lines) if node.lines else (node.line, 0)


def function_row(notes, node, member):
    """A function member: name, return type, line range (end 0 = unknown) and its hint."""
    return {"name": member.name, "type": member.type, "file": node.file, "line": member.line,
            "end": member.end, "hint": notes.value(function_key(node.id, member.name), "hint")}


def outline(graph, notes, node_id, function=None):
    """A class's (or file's) range, functions with line ranges, fields and nested types;
    only `function`'s row when one is named."""
    node = graph.nodes[node_id]
    first, last = node_span(node)
    functions = [function_row(notes, node, m) for m in node.members
                 if m.kind in FUNCTION_KINDS and function in (None, m.name)]
    fields = [] if function else [
        {"name": m.name, "kind": m.kind, "type": m.type, "file": node.file, "line": m.line}
        for m in node.members if m.kind in FIELD_KINDS]
    inner = [] if function else [
        {"id": n.id, "name": n.name, "kind": n.kind, "file": n.file, "line": node_span(n)[0],
         "end": node_span(n)[1]}
        for n in graph.nodes.values() if n.id.rpartition(".")[0] == node_id]
    return {"id": node_id, "name": node.name, "kind": node.kind, "file": node.file,
            "line": first, "end": last, "lines": node.lines, "span": node.span,
            "hint": notes.value(node_key(node_id), "hint"), "functions": functions,
            "fields": fields, "inner": sorted(inner, key=lambda n: n["line"])}


# find: points per matched word by where it matched (best place per word counts)
NAME_POINTS, EXACT_BONUS, HINT_POINTS, PATH_POINTS, NOTE_POINTS, DOC_POINTS = 10, 5, 5, 3, 2, 1
FUNCTION_NAME_POINTS = 8
NAMING_POINTS = (NAME_POINTS, FUNCTION_NAME_POINTS)
SNIPPET_MAX = 80


def match_score(words, places):
    """Sum over words of the best `points` among places (text, points) containing the word;
    None when a word matches nowhere. An exact name match earns a bonus."""
    total = 0
    for word in words:
        best = max((points + (EXACT_BONUS if points in NAMING_POINTS and text.lower() == word
                              else 0)
                    for text, points in places if text and word in text.lower()), default=None)
        if best is None:
            return None
        total += best
    return total


def snippet(text):
    text = " ".join((text or "").split())
    return text if len(text) <= SNIPPET_MAX else text[:SNIPPET_MAX - 1].rstrip() + "…"


def note_places(notes, key):
    """The hint (best), then description, rules and review of a notes key, as match places."""
    values = {name: notes.value(key, name) for name in ("hint", "description", "rules", "review")}
    return values, [(values["hint"], HINT_POINTS)] + [
        (values[name], NOTE_POINTS) for name in ("description", "rules", "review")]


def find_hit(unit, kind, file, line, end, score, hint):
    return {"unit": unit, "kind": kind, "file": file, "line": line, "end": end,
            "score": score, "hint": hint}


def find(graph, notes, words, tests=False):
    """Classes and functions matching every word in their name, path, notes or code doc,
    best first: [{unit, kind, file, line, end, score, hint}]."""
    words = [w.lower() for w in words]
    hits = []
    for node in graph.nodes.values():
        if node.kind == "external" or (is_test(node) and not tests):
            continue
        values, places = note_places(notes, node_key(node.id))
        score = match_score(words, [(node.name, NAME_POINTS), (node.id, PATH_POINTS),
                                    (node.file, PATH_POINTS), *places, (node.doc, DOC_POINTS)])
        if score is not None:
            hits.append(find_hit((node.id, None), node.kind, node.file, node.line, 0, score,
                                 values["hint"] or snippet(values["description"] or node.doc)))
        for member in node.members:
            if member.kind not in FUNCTION_KINDS:
                continue
            values, places = note_places(notes, function_key(node.id, member.name))
            score = match_score(words, [(member.name, FUNCTION_NAME_POINTS), (node.name, PATH_POINTS),
                                        *places])
            if score is not None:
                hits.append(find_hit((node.id, member.name), member.kind, node.file,
                                     member.line, member.end, score,
                                     values["hint"] or snippet(values["description"])))
    return sorted(hits, key=lambda h: (-h["score"], h["unit"][0], h["unit"][1] or ""))


def incoming_edges(graph):
    """to id -> edges from non-test classes."""
    index = {}
    for edge in graph.edges.values():
        if not is_test(graph.nodes.get(edge.from_id)):
            index.setdefault(edge.to, []).append(edge)
    return index


def callers_of(incoming, unit):
    """Units that use `unit`: a class's other users, or the functions calling a function
    (a caller with no known function, or an initializer, counts as its class)."""
    node_id, function = unit
    found = set()
    for edge in incoming.get(node_id, ()):
        if function is None:
            if edge.from_id != node_id:
                found.add((edge.from_id, None))
            continue
        for call in edge.calls:
            if called_function(call.member) == function:
                caller = calling_function(call.from_member)
                found.add((edge.from_id, None if caller == INIT_MEMBER else caller))
    return found


def impact(graph, unit, depth=IMPACT_DEPTH):
    """Who depends on a class or `Class#function`: {"levels": [[unit]] by distance (sorted),
    "tests": [(test file, via)] to rerun for it and every dependent found}."""
    incoming, seen, frontier, levels = incoming_edges(graph), {unit}, {unit}, []
    for _ in range(depth):
        frontier = {c for u in frontier for c in callers_of(incoming, u)} - seen
        if not frontier:
            break
        seen |= frontier
        levels.append(sorted(frontier, key=lambda u: (u[0], u[1] or "")))
    dependents = sorted({node_id for level in levels for node_id, _ in level} - {unit[0]})
    return {"levels": levels, "tests": retest_files(graph, unit[0], dependents)}


def brief(graph, notes, units):
    """Per class or `Class#function` unit what a brief needs: its outline, its node view
    (notes, warnings, covering tests, neighbour lines) and its impact."""
    items = []
    for node_id, function in units:
        node = graph.nodes[node_id]
        zoom = {"from": node_id, "to": None, "level": effective_level(graph, notes, node_id)}
        view = compute_view(graph, notes, zoom=zoom, expand=[EXPAND_ALL] if function else (),
                            show={"test": is_test(node)})
        items.append({"unit": (node_id, function), "outline": outline(graph, notes, node_id, function),
                      "view": view, "impact": impact(graph, (node_id, function))})
    return items
