"""The view engine (docs/DESIGN.md 5, 6, 6b, 7, 9): the only place views are computed.

`compute_view(graph, notes, ...)` returns a JSON-ready dict of visible nodes, faded outside
nodes and merged edges (direct or derived, with via paths and per-hop calls). The viewer and
the text CLI both draw this; neither computes views itself.
"""

import os
import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field

from .apps import AppError, candidates, parse_target
from .checks import split_ignored, warning_text
from .fanout import EXPAND_ALL, FIELD_KINDS, class_parts, fanout_links
from .model import (ENTRY_MECHANISMS, FUNCTION_KINDS, INIT_MEMBER, STRUCT_KINDS, called_function, calling_function,
                    is_comm)
from .overview import overview_json
from .planned import function_plan_json, hand_graph, plan_counts, plan_json
from .progress import add_view_progress
from .rpc_messages import is_message_group, message_hint, node_messages
from .notes import (ARROW, FINGERPRINTED, FUNCTION_SEP, TEXT_FIELDS, Notes, domain_id, edge_key,
                    function_key, is_function, node_key)
from .testcode import (covering_tests, function_role, index_tests, is_helper_function, is_test,
                       is_runnable_test, is_test_path, retest_files, tests_using, function_callers, direct_tests)
from .wordmatch import EXACT, PART, close_names, split_words, text_quality
from .textindex import mentions, search_text

ALL, CONCRETE = "all", "concrete"
CODE_AUTHOR = "code"  # `description_by` of a description taken from the code
OWNER_RANK = {"composition": 0, "association": 1, "dependency": 2}
LOADED_BY = "loaded-by"  # kind of the drawn line B -> A for "A is loaded by B" (notes, rules)
PLANNED_SHOW = "planned"  # `show` key: False leaves out planned classes, functions and lines (DESIGN 7f)
DEFAULT_SHOW = {"external": True, "comm": True, "test": False, PLANNED_SHOW: True}
SCENE_TYPE = ".tscn"  # Godot scenes: hiding them is hiding this type
HIDE_TYPES = "hide_types"  # `show` key: file extensions (`.gd`) whose classes are removed
HIDE_FILE_KINDS = "hide_file_kinds"  # `show` key: file kinds (`Data`) whose classes are removed
ONLY_TAGS = "tags"  # `show` key: tags; when set, classes with none of them are removed
HIDE_TAGS = "hide_tags"  # `show` key: tags whose classes are removed
HIDE_KINDS = "hide_kinds"  # `show` key: line kinds (`signal`) whose lines are not drawn
LINES = "lines"  # `show` key: EXPLAINED marks the lines without a hint `quiet` (else all equal)
EXPLAINED, ALL_LINES = "explained", "all"
UNSORTED = "Unsorted"  # the domain card of classes without a domain (domain focus, DESIGN 6b)
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


def file_type(file):
    """A file's type chip text: its lowercase extension (`.gd`), "" without one."""
    return os.path.splitext(file)[1].lower()


def is_pure_abstract(node):
    """An abstract class with no real code: only abstract members (or none)."""
    return ("abstract" in node.flags
            and all(m.kind in ABSTRACT_MEMBER_KINDS for m in node.members))


def raw_level(graph, notes, node_id):
    """A level set in notes wins over the scan's guess."""
    return notes.value(node_key(node_id), "level", graph.nodes[node_id].level)


def effective_level(graph, notes, node_id):
    """The node's level; one deeper than the app's last level counts as the last level
    (the only place that clamps)."""
    level = raw_level(graph, notes, node_id)
    last = (notes.levels or {}).get("count")
    return min(level, last) if last else level


def level_scale(graph, notes):
    """The app's levels as {"count": last level, "names": [name of level 0..count]}: what the
    user saved in settings.json (apps.py), else one level per scanned depth named "Level N".
    The one place for level names, used by the viewer, the CLI text and the view engine."""
    saved = notes.levels or {}
    count = saved.get("count") or max((raw_level(graph, notes, i) for i, n in graph.nodes.items()
                                       if n.kind != "external"), default=1)
    given = saved.get("names") or []
    names = [given[i] if i < len(given) and given[i] else ("Domains" if i == 0 else f"Level {i}")
             for i in range(count + 1)]
    return {"count": count, "names": names}


def level_label(scale, level):
    """`2 Systems`-style text for a level (the number alone when the name is the default)."""
    names = scale["names"] if scale else []
    name = names[level] if level < len(names) else ""
    return f"L{level} {name}" if name and name != f"Level {level}" else f"L{level}"


def level_counts(ctx):
    """Classes per level 0..count (level 0: the domains), for the viewer's depth ruler;
    follows the Show toggles."""
    count = level_scale(ctx.graph, ctx.notes)["count"]
    counts = [0] * (count + 1)
    counts[0] = len(ctx.notes.domains())
    for node_id in ctx.allowed:
        if not ctx.is_external(node_id):
            counts[min(ctx.level(node_id), count)] += 1
    return counts


def is_interface_like(node):
    return node.kind == "interface"


class ViewContext:
    """Graph + notes + view options, with the lookups every view step shares."""

    def __init__(self, graph, notes, view=ALL, show=None):
        if view not in (ALL, CONCRETE):
            raise ValueError(f"view must be {ALL!r} or {CONCRETE!r}")
        self.show = {**DEFAULT_SHOW, **(show or {})}
        graph = hand_graph(graph, notes, self.show[PLANNED_SHOW])  # + hand-made items (planned.py)
        self.graph, self.notes, self.view = graph, notes, view
        self.removed = {i for i, n in graph.nodes.items() if self._removed(n)}
        self.allowed = [i for i in graph.nodes if i not in self.removed]
        self._impls = {}
        self.loaders = notes.loaders(graph)  # class id -> the classes that load it by name
        self.links = list(self._make_links())
        self.incoming, self.outgoing, self.pair_calls = {}, {}, {}
        for link in self.links:
            self.incoming.setdefault(link.to, []).append(link)
            self.outgoing.setdefault(link.from_id, []).append(link)
            self.pair_calls.setdefault((link.from_id, link.to), []).extend(link.calls)
        self._owners = {}
        self._tests = None
        self.holders = {i.rpartition(".")[0] for i in graph.nodes}  # ids that hold a nested class

    def covering_tests(self, node_id):
        if self._tests is None:
            self._tests = index_tests(self.graph)
        return covering_tests(self.graph, node_id, self._tests)

    # --- node facts (notes override the scan) ---
    def title(self, node_id):
        """THE box title: the nickname, else the one function of a one-function module, else None."""
        return self.notes.nicknames().get(node_key(node_id)) or sole_function(self.graph.nodes[node_id], self.holders)

    def level(self, node_id):
        return effective_level(self.graph, self.notes, node_id)

    def domain(self, node_id):
        node = self.graph.nodes.get(node_id)
        return self.notes.domain_of(node) if node else None

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

    def _removed(self, node, ignore_types=False):
        """Switched-off types; hidden tests are removed outright, never folded (DESIGN 12a)."""
        if node.kind == "external" and not self.show.get("comm" if is_comm(node) else "external", True):
            return True
        if is_test(node) and not self.show.get("test"):
            return True
        if not ignore_types and node.file and (file_type(node.file) in self.show.get(HIDE_TYPES, ())
                                               or node.file_kind in self.show.get(HIDE_FILE_KINDS, ())):
            return True
        if not ignore_types and self.tag_removed(node):
            return True
        return self.view == CONCRETE and (is_interface_like(node) or is_pure_abstract(node))

    def tag_removed(self, node):
        """Switched off by the tag filter: a hidden tag, or none of the wanted tags."""
        only, hidden = self.show.get(ONLY_TAGS), self.show.get(HIDE_TAGS)
        if not only and not hidden:
            return False
        tags = set(self.notes.tags_of(node.id))
        return bool(tags & set(hidden or ())) or bool(only and not tags & set(only))

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
        for target, loaders in self.loaders.items():
            for loader in loaders:
                if target not in self.removed and loader["from"] not in self.removed:
                    yield Link(loader["from"], target, LOADED_BY, [], held=True)

    def owner(self, node_id):
        """Owner per DESIGN 5.1: composition, else association, else dependency source;
        ties go to the higher level (smaller number), then an owner in the hidden class's own
        domain, then the id."""
        if node_id not in self._owners:
            options = [l for l in self.incoming.get(node_id, [])
                       if l.kind in OWNER_RANK and l.from_id != node_id
                       and not is_comm(self.graph.nodes.get(l.from_id))]  # a message node holds nothing
            own_domain = self.domain(node_id)
            options.sort(key=lambda l: (OWNER_RANK[l.kind], self.level(l.from_id),
                                        not own_domain or self.domain(l.from_id) != own_domain,
                                        l.from_id))
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

    def __init__(self, ctx, visible, fold_domains=False, card_of=None):
        """`fold_domains`: a hidden class with a domain folds into its domain card (level 0).
        `card_of(node_id)`: the card id a hidden class folds into, or None (replaces it)."""
        self.ctx, self.visible = ctx, visible
        self.card_of = card_of or ((lambda n: domain_id(ctx.domain(n)) if ctx.domain(n) else None)
                                   if fold_domains else (lambda n: None))
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
        card = node_id in self.ctx.graph.nodes and self.card_of(node_id)
        if card:
            return [(card, (node_id,))]
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
    """DESIGN 6: the zoom set at one more level of detail, plus faded links out of it.

    A line whose hidden classes are all deeper skips to the first level that has some; then
    the zoom shows just the path (no faded links, the ends are terminations)."""
    level = zoom.get("level", 1)
    from_id, to = zoom["from"], zoom.get("to")
    shown = level + 1
    if to:
        via = zoom.get("via")
        if via is None:
            via = edge_via(ctx, from_id, to, level)
        via = [n for n in via if n in ctx.graph.nodes and n not in ctx.removed]
        shown = max(shown, min(map(ctx.level, via), default=shown))
        core = {from_id, to} | {n for n in via if ctx.level(n) <= shown}
    else:
        core = {from_id} | neighbours(ctx, from_id, level + 1)
    folder = Folder(ctx, ctx.visible_at(level) | core, fold_domains=level == 0)
    return focus_on(core, collapse(ctx, folder), folder, shown, outside=shown == level + 1)


def domain_view(ctx, name, level):
    """DESIGN 6b domain focus: one domain's classes at a level (at least 1); every other class
    folds into its domain's card (no domain: the Unsorted card; deeper ones first fold by the
    normal rules), and the cards and outside code it talks to are faded around it."""
    level = max(level, 1)
    domain_of = lambda n: ctx.domain(n) or UNSORTED
    visible = ctx.visible_at(level)
    core = {n for n in visible if not ctx.is_external(n) and domain_of(n) == name}
    card_of = lambda n: (None if domain_of(n) == name or (not ctx.domain(n) and ctx.level(n) > level)
                         else domain_id(domain_of(n)))
    folder = Folder(ctx, core | {n for n in visible if ctx.is_external(n)}, card_of=card_of)
    return focus_on(core, collapse(ctx, folder), folder, level)


def focus_on(core, edges, folder, level, outside=True):
    """Keep edges touching the core; fade the ones that leave it and their far ends.
    `outside` False keeps only the edges between core nodes."""
    kept = {k: e for k, e in edges.items()
            if (e.from_id in core and e.to in core) or (outside and (e.from_id in core or e.to in core))}
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
    data["near"] = sorted(near)
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
    """Lines and members (with how many are functions and fields); `span` False means a
    file-level unit (module, script): its lines are the file's lines outside its types."""
    kinds = [m.kind for m in node.members]
    return {"lines": node.lines, "members": len(kinds), "span": node.span,
            "functions": sum(k in FUNCTION_KINDS for k in kinds),
            "fields": sum(k in FIELD_KINDS for k in kinds),
            "properties": sum(m.kind in FIELD_KINDS and m.public for m in node.members)}


def warnings_json(warnings):
    return [{**w, "text": warning_text(w)} for w in warnings]


def node_json(ctx, node_id, faded):
    notes = ctx.notes
    if node_id not in ctx.graph.nodes:  # a domain card
        name = node_id.split(":", 1)[1]
        return {"id": node_id, "name": name, "kind": "domain", "level": 0, "flags": [],
                "file": "", "line": 0, "faded": faded, "domain": name, "dep": None,
                "lines": 0, "members": 0, "span": False, "functions": 0, "fields": 0, "properties": 0,
                "warnings": [], "ignored_warnings": [], "ignore_warnings": [], "tested_by": [], "loaders": [],
                **note_json(notes, node_key(node_id))}
    node = ctx.graph.nodes[node_id]
    flags = [f for f in node.flags if f != "singleton"]
    if ctx.singleton(node_id):
        flags.append("singleton")
    ignore = notes.ignored_warnings(node_id)
    shown, ignored = split_ignored(node, notes)
    notes_json = with_code_doc(note_json(notes, node_key(node_id), graph=ctx.graph), node.doc)
    return {"id": node_id, "name": node.name, "kind": node.kind, "level": ctx.level(node_id),
            "title": notes_json["nickname"] or sole_function(node, ctx.holders),  # THE box title, else `name`
            "flags": flags, "file": node.file, "line": node.line, "faded": faded,
            "replaced_by": node.replaced_by, "hub_degree": node.hub_degree, "file_kind": node.file_kind, "domain": ctx.domain(node_id),
            "domain_by": notes.domain_and_author(node)[1],
            "dep": node.namespace if node.kind == "external" else None,
            "comm": node.comm or None, "rpcs": [m.name for m in node.members if m.rpc and not node.comm],
            "messages": node_messages(ctx.graph, node, ctx.outgoing.get(node_id, [])),
            **size_json(node), "warnings": warnings_json(shown),
            "ignored_warnings": warnings_json(ignored), "ignore_warnings": ignore,
            "tested_by": ctx.covering_tests(node_id) if node.kind != "external" else [],
            "loaders": [{**l, "name": ctx.graph.nodes[l["from"]].name}
                        for l in ctx.loaders.get(node_id, [])],
            **notes_json, **plan_json(notes, ctx.graph, node)}


def edge_hint(ctx, edge):
    """(hint, author, note key): the pair's own note, else the note on `from -> interface`."""
    keys = [edge_key(edge.from_id, edge.to)] + [edge_key(f, i) for f, i in sorted(edge.as_from)]
    for key in keys:
        hint, by = ctx.notes.get(key, "hint")
        if hint is not None:
            return hint, by, key
    return None, None, None


def hop_pairs(edge, path):
    """The (a, b) hops a derived line stands for along one hidden path."""
    ids = [edge.from_id, *path, edge.to]
    return list(zip(ids, ids[1:]))


def is_explained(ctx, edge):
    """True when the line, or any hop it stands for, has a hint."""
    if edge_hint(ctx, edge)[0]:
        return True
    return any(ctx.notes.value(edge_key(a, b), "hint")
               for path in edge.paths for a, b in hop_pairs(edge, path))


def quiet_keys(ctx, edges):
    """Keys of the lines drawn faint and listed last (`show["lines"]` EXPLAINED): no hint on the
    line or its hops. The one rule for which lines are quiet (viewer, fan-out, slim text)."""
    if ctx.show.get(LINES) != EXPLAINED:
        return set()
    return {edge.key for edge in edges if not is_explained(ctx, edge)}


def add_used_by(node_rows, edges):
    """`used_by` on each class row: how many other classes on this map have a line to it (the
    box face's importance dots and "used by N" badge)."""
    users = {}
    for edge in edges:
        if edge.from_id != edge.to:
            users.setdefault(edge.to, set()).add(edge.from_id)
    for row in node_rows:
        row["used_by"] = len(users.get(row["id"], ()))


def domain_notes(ctx, node_rows):
    """{domain: {hint, description}} for the domains of the shown classes (frame headers)."""
    names = sorted({row["domain"] for row in node_rows if row.get("domain")})
    return {name: {field: ctx.notes.value(node_key(domain_id(name)), field)
                   for field in ("hint", "description")} for name in names}


def hops_json(ctx, edge, path):
    def calls(a, b):
        pairs = {(c.from_member, c.member) for c in ctx.pair_calls.get((a, b), [])}
        return [{"member": m, "from_member": f}
                for f, m in sorted(pairs, key=lambda c: (c[1], c[0] or ""))]
    return [{"from": a, "to": b, "calls": calls(a, b)} for a, b in hop_pairs(edge, path)]


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
                 focus=None, expand=(), badges=None, domain=None, open_near=False, track=None):
    """The view for (level, view, type toggles, optional zoom, optional focus).

    zoom: {"from", "to" (None for a node zoom), "level", "via" (optional: found if missing)}.
    In a zoom the returned "level" is the level shown (zoom level + 1, or deeper when the
    line's hidden classes all are).
    expand: class ids to open into their functions (EXPAND_ALL = every shown class); only
    shown classes open. Adds "expanded" and "fanout" (fanout.py, DESIGN 12c).
    focus: targets as view ids (`A`, `A→B`, `A#f`) to highlight, as specs and tour steps do
    (DESIGN 7, 7c); a function target also opens its class. "focus" in the result lists the
    highlighted ids ({"nodes", "edges", "functions", "near"}), None without a focus in the graph;
    "near" = the classes a focused function really calls or is called by, opened too with
    open_near (a tour step shows which of their functions).
    badges: {view id (`A`, `A→B`): count} of cards linking it (cards.link_index); each shown
    class and line then gets "cards" = its count (DESIGN 7d).
    domain: a domain name (or UNSORTED) to show alone at the level, the other domains as faded
    cards (DESIGN 6b domain focus); a zoom wins over it. "domain" in the result echoes it.
    track: a progress track's name (progress.py): each box and overview domain gets its numbers
    as "progress", the result "progress" = the whole app's (None: no track or an unknown one).
    """
    ctx = ViewContext(graph, notes or Notes(), view, show)
    graph = ctx.graph  # with the hand-made items
    if zoom:
        zoom = dict(zoom)
        if zoom.get("to") and zoom.get("via") is None:
            zoom["via"] = edge_via(ctx, zoom["from"], zoom["to"], zoom.get("level", 1))
        result = zoom_view(ctx, zoom)
    elif domain:
        result = domain_view(ctx, domain, level)
    else:
        result = level_view(ctx, level)
    hide_lines(ctx, result)
    focused = None
    if focus:
        result, focused = target_focus(result, graph, focus)
        if focused:
            expand = [*expand, *(f.rpartition(FUNCTION_SEP)[0] for f in focused["functions"]),
                      *(focused["near"] if open_near else ())]
    nodes = [node_json(ctx, i, False) for i in result.shown]
    nodes += [node_json(ctx, i, True) for i in result.faded]
    edges = sorted(result.edges.items())
    open_ids = expanded_ids(ctx, result, expand)
    quiet = quiet_keys(ctx, [e for _, e in edges])
    add_used_by(nodes, [e for _, e in edges])
    edge_rows = [{**edge_json(ctx, e), "fanned": e.from_id in open_ids or e.to in open_ids,
                  "quiet": e.key in quiet} for _, e in edges]
    if badges is not None:
        for row in nodes:
            row["cards"] = badges.get(row["id"], 0)
        for row in edge_rows:
            row["cards"] = badges.get(row["key"], 0)
    scale = level_scale(graph, ctx.notes)
    overview = overview_json(ctx, edge_rows) if result.level == 0 and not zoom and not domain else None
    track_numbers = add_view_progress(ctx, result, nodes, overview, track) if track else None
    return {"level": result.level, "view": view, "zoom": zoom, "domain": None if zoom else domain,
            "scale": {**scale, "counts": level_counts(ctx)},
            "counts": {"shown": len(result.shown), "total": len(ctx.allowed)},
            "plan": plan_counts(graph),
            "types": type_counts(ctx, nodes),
            "hidden_types": sorted(ctx.show.get(HIDE_TYPES, ())),
            "file_kinds": file_kind_counts(ctx, nodes),
            "hidden_file_kinds": sorted(ctx.show.get(HIDE_FILE_KINDS, ())),
            "tags": tag_counts(ctx, nodes),
            "hidden_tags": sorted(ctx.show.get(HIDE_TAGS, ())),
            "only_tags": sorted(ctx.show.get(ONLY_TAGS) or ()),
            "hidden_kinds": sorted(ctx.show.get(HIDE_KINDS, ())),
            "lines": EXPLAINED if ctx.show.get(LINES) == EXPLAINED else ALL_LINES,
            "nodes": sorted(nodes, key=sort_key),
            "domains": domain_notes(ctx, nodes),
            "overview": overview,
            "progress": track_numbers,
            "edges": edge_rows,
            "needs_binding": ctx.needs_binding(),
            "focus": focused,
            "expanded": {i: expanded_json(ctx, i) for i in sorted(open_ids)},
            "fanout": fanout_links(ctx, [e for _, e in edges], open_ids, quiet)}


# --- start paths (DESIGN 7c, "Start here") ---

START_MIN, START_MAX = 2, 6  # a start path has 2-6 steps: it stops at 6 or at a dead end (aim: 4-6)
START_PATHS = 2  # at most this many entry classes get a path


def start_entries(ctx, level):
    """Entry classes shown at `level`, app starts (godot-main, program-main, ...) before tools
    (script-main), then the plain `entry` flag; the order of model.ENTRY_MECHANISMS."""
    shown = level_view(ctx, level).shown
    rank = lambda node: next((i for i, f in enumerate(ENTRY_MECHANISMS) if f in node.flags),
                             len(ENTRY_MECHANISMS))
    nodes = [ctx.graph.nodes[i] for i in shown if i in ctx.graph.nodes
             and "entry" in ctx.graph.nodes[i].flags and not ctx.is_external(i)]
    return [n.id for n in sorted(nodes, key=lambda n: (rank(n), n.id))]


def start_path(ctx, entry, level=1):
    """The main path from the entry class `entry`: [{id, hint, calls}] (`hint` and `calls` = the
    line that leads into that step; the first step has none).

    Begin at the entry class. At each step look at the lines leaving it in the map at `level`
    and follow the one with the best score: lines with a hint first, then the most calls. Skip hubs
    (their lines say nothing about this flow), external code and classes already on the path.
    Stop at START_MAX (6) steps or at a dead end (no line left to follow): a short path is
    returned as it is, start_paths() drops the ones under START_MIN."""
    edges = level_view(ctx, level).edges.values()
    leaving = {}
    for edge in edges:
        leaving.setdefault(edge.from_id, []).append(edge)
    path, seen = [{"id": entry, "hint": None, "calls": 0}], {entry}
    while len(path) < START_MAX:
        options = []
        for edge in leaving.get(path[-1]["id"], []):
            node = ctx.graph.nodes.get(edge.to)
            if node is None or edge.to in seen or ctx.is_external(edge.to) or "hub" in node.flags:
                continue
            hint = edge_hint(ctx, edge)[0]
            options.append((not hint, -sum(edge.calls.values()), edge.to, hint))
        if not options:
            break
        _, calls, to, hint = min(options, key=lambda option: option[:3])  # hint, calls, then id
        path.append({"id": to, "hint": hint, "calls": -calls})
        seen.add(to)
    return path


def start_paths(ctx, level=1):
    """Start paths for the first START_PATHS entry classes whose path has START_MIN steps or more,
    each a start_path() list."""
    paths = (start_path(ctx, entry, level) for entry in start_entries(ctx, level))
    return [p for p in paths if len(p) >= START_MIN][:START_PATHS]


def hide_lines(ctx, result):
    """Drop the lines whose kinds are all hidden (`show["hide_kinds"]`); classes stay."""
    hidden = set(ctx.show.get(HIDE_KINDS, ()))
    if hidden:
        result.edges = {key: e for key, e in result.edges.items() if not e.kinds <= hidden}


def strip_counts(ctx, node_rows, hide_key, label_of_row, label_of_node):
    """{label: classes} for a strip of toggles: the drawn classes, plus the hidden labels
    (count of their classes this view's toggles allow) so their chips can be turned back on."""
    counts = Counter(label_of_row(row) for row in node_rows if row["file"])
    for hidden in ctx.show.get(hide_key, ()):
        counts[hidden] = sum(1 for n in ctx.graph.nodes.values() if n.file and label_of_node(n) == hidden
                             and not ctx._removed(n, ignore_types=True))
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def type_counts(ctx, node_rows):
    """{extension: classes} for the types strip."""
    return strip_counts(ctx, node_rows, HIDE_TYPES, lambda row: file_type(row["file"]),
                        lambda node: file_type(node.file))


def tag_counts(ctx, node_rows):
    """{tag: classes} for the tag chips: the drawn classes' tags, plus the hidden tags (count of
    their classes the other toggles allow) so their chips can be turned back on."""
    counts = Counter(tag for row in node_rows for tag in row.get("tags") or ())
    for hidden in ctx.show.get(HIDE_TAGS, ()):
        counts[hidden] = sum(1 for n in ctx.graph.nodes.values() if hidden in ctx.notes.tags_of(n.id)
                             and not ctx._removed(n, ignore_types=True))
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def file_kind_counts(ctx, node_rows):
    """{file kind: classes} for the kinds in the types strip."""
    return strip_counts(ctx, node_rows, HIDE_FILE_KINDS, lambda row: row["file_kind"],
                        lambda node: node.file_kind)


def expanded_json(ctx, node_id):
    """An open class: its functions (with their notes) and its fields/signals."""
    parts = class_parts(ctx, node_id)
    node = ctx.graph.nodes[node_id]
    members = {m.name: m for m in node.members}
    parts["functions"] = [{**f, **note_json(ctx.notes, function_key(node_id, f["name"]),
                                                    graph=ctx.graph),
                           **(function_plan_json(ctx.notes, node, members[f["name"]])
                              if f["name"] in members else {})}
                          for f in parts["functions"]]
    return parts


def expanded_ids(ctx, result, expand):
    """The shown graph classes (not outside code or domain cards) and message nodes to open into
    their functions (messages)."""
    wanted = result.shown if EXPAND_ALL in expand else set(expand) & result.shown
    return {i for i in wanted if i in ctx.graph.nodes
            and (not ctx.is_external(i) or is_message_group(ctx.graph.nodes[i]))}


# --- reading helpers for the CLI: outline, find, impact (docs/DESIGN.md 11) ---

IMPACT_DEPTH = 3
MENTION_KINDS = ("test", "doc")  # names index kinds impact lists as mentions
Unit = tuple  # (class id, function name or None): what impact and brief look at


def node_span(node):
    """(first, last) line of a node: its own span, else its whole file; last 0 if unknown."""
    if node.span:
        return node.line, node.line + node.lines - 1
    return (1, node.file_lines) if node.file_lines else (node.line, 0)


def function_row(notes, node, member):
    """A function member: name, return type, line range (end 0 = unknown), nickname and hint."""
    key = function_key(node.id, member.name)
    return {"name": member.name, "type": member.type, "file": node.file, "line": member.line,
            "end": member.end, "nickname": notes.value(key, "nickname"), "hint": notes.value(key, "hint")}


def outline(graph, notes, node_id, function=None):
    """A class's (or file's) range, functions with line ranges, fields and nested types;
    only `function`'s row when one is named."""
    node = graph.nodes[node_id]
    first, last = node_span(node)
    functions = [function_row(notes, node, m) for m in node.members
                 if m.kind in FUNCTION_KINDS and function in (None, m.name)]
    fields = [] if function else [
        {"name": m.name, "kind": m.kind, "type": m.type, "file": node.file, "line": m.line,
         "public": m.public}
        for m in node.members if m.kind in FIELD_KINDS]
    inner = [] if function else [
        {"id": n.id, "name": n.name, "kind": n.kind, "file": n.file, "line": node_span(n)[0],
         "end": node_span(n)[1]}
        for n in graph.nodes.values() if n.id.rpartition(".")[0] == node_id]
    return {"id": node_id, "name": node.name, "kind": node.kind, "file": node.file,
            "line": first, "end": last, "lines": node.lines, "span": node.span,
            "global": global_row(graph, node_id) if node.global_name and not is_test(node) else None,
            "globals": globals_list(graph) if is_test(node) and not function else [],
            "nickname": notes.value(node_key(node_id), "nickname"),
            "hint": notes.value(node_key(node_id), "hint"), "functions": functions,
            "fields": fields, "inner": sorted(inner, key=lambda n: n["line"])}


def function_last(node, member):
    """A function's last line: its own end, else the line before the next member, else the end of
    its class (0 = unknown, to the end of the file)."""
    if member.end >= member.line:
        return member.end
    later = [m.line for m in node.members if m.line > member.line]
    return min(later) - 1 if later else node_span(node)[1]


def unit_at(graph, file, line):
    """(class id, function or None) of the innermost class and function around `file:line`
    (a repo path, or the end of one), else None."""
    nodes = [n for n in graph.nodes.values() if n.kind != "external" and n.file
             and (n.file == file or n.file.endswith("/" + file))]
    around = [n for n in nodes if node_span(n)[0] <= line <= (node_span(n)[1] or line)]
    if not around:
        return None
    node = max(around, key=lambda n: (n.span, node_span(n)[0]))
    functions = [m for m in node.members if m.kind in FUNCTION_KINDS
                 and m.line <= line <= (function_last(node, m) or line)]
    return node.id, max(functions, key=lambda m: m.line).name if functions else None


def find_text(graph, index, root, pattern, path=""):
    """`find --text`: the names index's text matches (textindex.search_text), each with `unit`
    (class id, function or None) around it, from `unit_at`."""
    return [{**row, "unit": unit_at(graph, row["file"], row["line"]) if row["kind"] != "doc" else None}
            for row in search_text(index, root, pattern, path)]


READ_NEAR = 5  # lines around a `path:line` outside any function, unless a context is given


def with_context(ranges, context):
    context = context or 0
    return [(max(first - context, 1), last + context if last else 0) for first, last in ranges]


def read_blocks(graph, notes, unit, signatures=False, context=None):
    """What `read` prints for a class or Class#function: one block per function (overloads too) or
    the class, {unit, file, line, end, hint, ranges}; `ranges` = the (first, last) lines to print
    (last 0 = to the end of the file): the whole range, or one line per function signature, each
    widened by `context` lines."""
    node_id, function = unit
    node = graph.nodes[node_id]
    members = [m for m in node.members if m.kind in FUNCTION_KINDS and m.line > 0]
    if function:
        blocks = [{"unit": unit, "file": node.file, "line": m.line, "end": function_last(node, m),
                   "hint": notes.value(function_key(node_id, function), "hint")}
                  for m in members if m.name == function]
    else:
        first, last = node_span(node)
        blocks = [{"unit": unit, "file": node.file, "line": first, "end": last,
                   "hint": notes.value(node_key(node_id), "hint")}]
    for block in blocks:
        block["ranges"] = ([(m.line, m.line) for m in members
                            if block["line"] <= m.line <= (block["end"] or m.line)]
                           if signatures else [(block["line"], block["end"])])
        block["ranges"] = with_context(block["ranges"], context)
    return blocks


def read_place(graph, notes, file, line, signatures=False, context=None):
    """`read`'s blocks for `file:line`: the function around it, else the lines near it."""
    unit = unit_at(graph, file, line)
    if unit and unit[1]:
        return read_blocks(graph, notes, unit, signatures, context)
    file = graph.nodes[unit[0]].file if unit else file
    return [{"unit": None, "file": file, "line": line, "end": line, "hint": None,
             "ranges": with_context([(line, line)], READ_NEAR if context is None else context)}]


def file_block(file, first, last, lines):
    """A `read` block for plain lines `first`-`last` of a file (clamped to the file's end)."""
    last = min(last, len(lines)) if lines else last
    return {"unit": None, "file": file, "line": first, "end": last, "hint": None,
            "ranges": [(first, last)]}


def targets_named(graph, name, namer):
    """Read targets a bare name stands for: classes called `name` and `Class#name` functions
    (code before tests; `namer(id)` = the short name to type)."""
    found = {}
    for node in graph.nodes.values():
        if node.kind == "external":
            continue
        if node.name == name:
            found[namer(node.id)] = node
        if any(m.name == name and m.kind in FUNCTION_KINDS for m in node.members):
            found[f"{namer(node.id)}#{name}"] = node
    code = {t: n for t, n in found.items() if not is_test(n)}
    return sorted(code or found)


SUGGESTIONS = 5  # most "did you mean" targets


def suggest_targets(graph, namer, query, limit=SUGGESTIONS):
    """Ready-to-paste read targets for the names nearest to `query` (the matcher `find` uses)."""
    function = query.rpartition("#")[2] or query
    targets = []
    for name in find_suggestions(graph, [function]):
        targets += [t for t in targets_named(graph, name, namer) if t not in targets]
    mine = query.rpartition("#")[0]  # `Class#typo`: that class's own functions first
    return sorted(targets, key=lambda t: not (mine and t.startswith(mine + "#")))[:limit]


def nicknamed_targets(graph, notes, nickname, namer):
    """Ready-to-paste targets (`Class`, `Class#function`) of the units nicknamed `nickname`."""
    return sorted(namer(node_id) + (f"#{function}" if function else "")
                  for node_id, function in notes.nicknamed(graph, nickname))


def sole_function(node, holders):
    """The one function of a module node that holds no classes: the name
    its box wears (`title`) in place of the file name. None for any other node. `holders`:
    ids that hold a nested class."""
    functions = [m.name for m in node.members if m.kind in FUNCTION_KINDS]
    if node.kind != "module" or node.span or node.id in holders or len(functions) != 1:
        return None
    return None if repeats_name(functions[0], node.name) else functions[0]


def repeats_name(function, module_name):
    """True when the function name holds the module's own name (`FUN_000414ee` in `414ee`,
    `F_4694d` in `4694d`: decompiler defaults), so a title would only repeat the id."""
    return (module_name.lower().lstrip("0") or module_name) in function.lower()


def display_name(name, title):
    """THE text form of a name with a title (DESIGN 10): `update_world (F_243e0)`; the bare
    name without one. The title is the nickname, else a one-function module's function
    (sole_function). Every text list of names prints through it, always "title (id)"."""
    return f"{title} ({name})" if title and title != name else name


class NickNamer:
    """A namer for text output that shows titles: `namer(id)` names a class and
    `namer.function(id, fn)` one of its functions, both through display_name. With `graph`,
    a class's title is also its sole function (what node_json's `title` says)."""

    def __init__(self, namer, notes, graph=None):
        self.namer, self.nicknames, self.graph = namer, notes.nicknames() if notes else {}, graph
        self.holders = {i.rpartition(".")[0] for i in graph.nodes} if graph else set()

    def title(self, node_id):
        node = self.graph.nodes.get(node_id) if self.graph else None
        return self.nicknames.get(node_key(node_id)) or (sole_function(node, self.holders) if node else None)

    def __call__(self, node_id):
        return display_name(self.namer(node_id), self.title(node_id))

    def function(self, node_id, function):
        return display_name(function, self.nicknames.get(function_key(node_id, function)))


def title_namer(graph, notes):
    """unique_namer with titles (NickNamer): the namer for text lists of classes."""
    return NickNamer(unique_namer(graph), notes, graph)


def unique_namer(graph):
    """Short name where it is unique in the graph, else the full id (lists across the repo)."""
    counts = Counter(n.name for n in graph.nodes.values())
    return lambda i: graph.nodes[i].name if counts[graph.nodes[i].name] == 1 else i


def miss(error, suggestions):
    """An AppError saying what was not found and which targets are nearest (`find`'s matcher)."""
    return AppError(f"{error}; did you mean: {', '.join(suggestions)}" if suggestions else str(error))


def checked_target(graph, notes, text):
    """`parse_target`, plus a `Name#function` must name a real function (or `module#Class`)."""
    kind, *ids = parse_target(graph, notes, text)
    if kind == "function" and not is_function(graph, "#".join(ids)):
        inner = f"{ids[0]}.{ids[1]}"  # `module#Class`: a class inside that module
        if inner in graph.nodes:
            return "node", inner
        own = [function for node_id, function in notes.nicknamed(graph, ids[1])
               if node_id == ids[0] and function]
        if len(own) == 1:  # `Class#nickname`
            return "function", ids[0], own[0]
        raise AppError(f"{ids[0]} has no function {ids[1]}")
    return (kind, *ids)


def resolve_target(graph, notes, text):
    """THE resolver of a code target, for every command that takes one: ("edge", A, B) for `A>B`,
    ("function", id, fn) for `Name#fn`, ("node", id) for `Name`, or a bare function name (one
    match is used; several: the AppError lists them as ready-to-paste targets). A miss says
    `did you mean` (`suggest_targets`). An ambiguous class name keeps its own error."""
    try:
        return checked_target(graph, notes, text)
    except AppError as error:
        if candidates(graph, text):
            raise
        namer = unique_namer(graph)
        found = targets_named(graph, text, namer) if not set("#>→") & set(text) else []
        found = found or nicknamed_targets(graph, notes, text, namer)
        if len(found) == 1:
            return checked_target(graph, notes, found[0])
        if found:
            raise AppError(f"{text} is in several places; pick one:\n  " + "\n  ".join(found))
        nicknames = close_names([text], sorted(set(notes.nicknames().values())))
        raise miss(error, (suggest_targets(graph, namer, text) + nicknames)[:SUGGESTIONS])


def suggest_files(graph, path, limit=5):
    """Scanned files nearest to a mistyped `path` (by file name)."""
    files = sorted({n.file for n in graph.nodes.values() if n.file})
    near = close_names([posixpath.splitext(posixpath.basename(path))[0]],
                       [posixpath.splitext(posixpath.basename(f))[0] for f in files])
    return [f for f in files if posixpath.splitext(posixpath.basename(f))[0] in near][:limit]


def files_named(graph, name):
    """Scanned files a module stem or name stands for (`resolver`, `coderstanding.resolver`,
    `coderstanding/resolver`): code before tests; `name` with its extension or a full path
    matches exactly."""
    wanted = name.replace(".", "/")
    files = sorted({n.file for n in graph.nodes.values() if n.file and n.kind != "external"})
    found = [f for f in files if f == name or f.endswith("/" + name)
             or posixpath.splitext(f)[0] in (wanted, name) or posixpath.splitext(f)[0].endswith("/" + wanted)]
    return [f for f in found if not is_test_path(f)] or found


def resolve_unit(graph, notes, text):
    """(class id, function or None) for a class or function target; edges are refused."""
    kind, *ids = resolve_target(graph, notes, text)
    if kind == "edge":
        raise AppError(f"{text}: give a class or Class#function, not a connection")
    if ids[0] not in graph.nodes:
        raise AppError(f"{text}: not a class")
    return ids[0], ids[1] if kind == "function" else None


def resolve_class(graph, notes, text):
    """Id of the class a target stands for (a function target = its class); also a domain."""
    kind, *ids = resolve_target(graph, notes, text)
    if kind == "edge":
        raise AppError(f"{text}: give a class, not a connection")
    return ids[0]


def selection_id(graph, notes, text):
    """The view's id of a selection: a node id, an edge key `A→B`, or `id#function`."""
    kind, *ids = resolve_target(graph, notes, text)
    return {"edge": "→", "function": "#"}.get(kind, "").join(ids)


def file_defs(graph, file, namer):
    """(first definition line, targets in file order) of a scanned file: its classes and
    top-level functions."""
    found = []
    for node in graph.nodes.values():
        if node.file != file:
            continue
        if node.span:
            found.append((node.line, namer(node.id)))
            continue
        found += [(m.line, f"{namer(node.id)}#{m.name}") for m in node.members
                  if m.kind in FUNCTION_KINDS and m.line > 0]
    found.sort()
    return (found[0][0] if found else 0), [target for _, target in found]


def header_block(file, lines, first_def):
    """The lines before the first function or class (imports, constants), minus the blank lines
    and decorators just above it; the whole file when it has no definitions."""
    end = trim_blank_end(lines, first_def - 1 if first_def else len(lines))
    while end > 1 and lines[end - 1].lstrip().startswith("@"):
        end = trim_blank_end(lines, end - 1)
    return file_block(file, 1, max(end, 1), lines)


HEADING = re.compile(r"(#{1,6})\s+(.+?)\s*#*\s*$")


def markdown_headings(lines):
    """[(line, level, text)] of a Markdown file's headings (code fences skipped)."""
    found, fenced = [], False
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = None if fenced else HEADING.fullmatch(line)
        if match:
            found.append((number, len(match[1]), match[2]))
    return found


def trim_blank_end(lines, end):
    while end > 1 and not lines[end - 1].strip():
        end -= 1
    return end


def markdown_sections(lines, heading):
    """[(first, last, text)] of the sections whose heading is `heading` (case-folded); a heading
    only starting with or containing it counts when nothing matches exactly. A section runs to
    the next heading of the same or a higher level."""
    headings = markdown_headings(lines)
    query = heading.strip().lower()
    for fits in (lambda t: t == query, lambda t: t.startswith(query), lambda t: query in t):
        picked = [h for h in headings if fits(h[2].lower())]
        if picked:
            ends = [next((n - 1 for n, level2, _ in headings if n > first and level2 <= level),
                         len(lines)) for first, level, _ in picked]
            return [(first, trim_blank_end(lines, end), text)
                    for (first, _, text), end in zip(picked, ends)]
    return []


# --- globals: names code reaches without importing, and how tests and code reach them ---

def lookup_index(graph):
    """global name -> {"tests": [forms], "code": [forms]}: the scene-tree lookups written in
    test files and in other code (distinct, in file order)."""
    index = {}
    for node in sorted(graph.nodes.values(), key=lambda n: (n.file, n.line)):
        for lookup in node.lookups:
            forms = index.setdefault(lookup["name"], {"tests": [], "code": []})
            where = forms["tests" if is_test(node) else "code"]
            if lookup["form"] not in where:
                where.append(lookup["form"])
    return index


def global_row(graph, node_id, index=None):
    """{name, kind, id, file, tests, code}: one global and the lookups that reach it."""
    node = graph.nodes[node_id]
    forms = (lookup_index(graph) if index is None else index).get(node.global_name, {})
    return {"name": node.global_name, "kind": node.global_kind, "id": node.id, "file": node.file,
            "tests": forms.get("tests", []), "code": forms.get("code", [])}


def globals_list(graph):
    """Every global of non-test code (autoloads, class_name scripts, [GlobalClass] classes),
    sorted by name, with how tests and code reach it."""
    index = lookup_index(graph)
    return sorted((global_row(graph, n.id, index) for n in graph.nodes.values()
                   if n.global_name and not is_test(n)), key=lambda row: row["name"])


# --- setup: what a test file relies on, so a new test can copy it ---

def own_functions(graph, node_ids):
    """(node, member) for the functions the nodes declare."""
    return [(graph.nodes[i], m) for i in node_ids for m in graph.nodes[i].members
            if m.kind in FUNCTION_KINDS]


def base_chain(graph, node_ids):
    """Ids of the in-repo bases (and their bases) of the nodes, nearest first."""
    chain, queue = [], list(node_ids)
    while queue:
        current = queue.pop(0)
        for edge in graph.edges.values():
            if edge.from_id == current and edge.kind == "generalization" \
                    and edge.to in graph.nodes and edge.to not in chain and edge.to not in node_ids:
                chain.append(edge.to)
                queue.append(edge.to)
    return chain


def role_rows(notes, members, role):
    """Function rows of one role (`hook` / `helper`; test functions and lifecycle ones are not)."""
    return [function_row(notes, node, m) for node, m in members
            if function_role(m.name) == role and (role != "helper" or is_helper_function(m.name))]


def nodes_of_file(graph, path):
    return sorted(n.id for n in graph.nodes.values() if n.file == path and n.kind != "external")


def used_globals(graph, node_ids, bases, index):
    """Global rows a test file relies on (edges to them, or lookups by name), each with
    `here`: the lookup forms written in the file or its bases."""
    names = {graph.nodes[e.to].global_name: e.to for e in graph.edges.values()
             if e.from_id in node_ids and e.to in graph.nodes and graph.nodes[e.to].global_name
             and not is_test(graph.nodes[e.to])}
    forms = {}
    for node_id in [*node_ids, *bases]:
        for lookup in graph.nodes[node_id].lookups:
            if lookup["form"] not in forms.setdefault(lookup["name"], []):
                forms[lookup["name"]].append(lookup["form"])
    by_name = {n.global_name: n.id for n in graph.nodes.values()
               if n.global_name and not is_test(n)}
    rows = []
    for name in sorted(set(names) | (set(forms) & set(by_name))):
        row = global_row(graph, names.get(name) or by_name[name], index)
        rows.append({**row, "here": forms.get(name, [])})
    return rows


def support_nodes(graph, node_ids, bases):
    """Other test-side nodes the file calls (helper scripts, fixtures): {name, file, called}."""
    found = {}
    for edge in graph.edges.values():
        target = graph.nodes.get(edge.to)
        if edge.from_id in node_ids and is_test(target) and edge.to not in bases \
                and edge.to not in node_ids:
            row = found.setdefault(edge.to, {"name": target.name, "file": target.file, "called": []})
            for call in edge.calls:
                member = called_function(call.member)
                if member not in row["called"]:
                    row["called"].append(member)
    return sorted(found.values(), key=lambda r: r["file"])


def setup_of(graph, notes, path, index=None, with_missing=True):
    """What the test file at `path` relies on: its hooks and helpers, the bases and their
    helpers, test-side nodes it calls, the globals it uses and how it reaches them; and
    (`missing`) fixtures that most sibling test files in its folder have and it lacks."""
    ids = nodes_of_file(graph, path)
    index = lookup_index(graph) if index is None else index
    bases = base_chain(graph, ids)
    members = own_functions(graph, ids)
    setup = {
        "file": path, "extends": graph.nodes[ids[0]].base, "tests": sum(
            1 for _, m in members if function_role(m.name) == "test"),
        "hooks": role_rows(notes, members, "hook"),
        "helpers": role_rows(notes, members, "helper"),
        "bases": [{"name": graph.nodes[b].name, "file": graph.nodes[b].file,
                   "hooks": [r["name"] for r in role_rows(notes, own_functions(graph, [b]), "hook")],
                   "helpers": [r["name"] for r in role_rows(notes, own_functions(graph, [b]), "helper")]}
                  for b in bases],
        "support": support_nodes(graph, ids, bases),
        "globals": used_globals(graph, ids, bases, index),
    }
    setup["missing"] = missing_fixtures(graph, notes, path, setup, index) if with_missing else []
    return setup


def fixture_keys(setup):
    """The (kind, name) fixtures of a setup, inherited ones included."""
    keys = {("hook", r["name"]) for r in setup["hooks"]} | {("helper", r["name"]) for r in setup["helpers"]}
    for base in setup["bases"]:
        keys |= {("base", base["name"])} | {("hook", n) for n in base["hooks"]} \
            | {("helper", n) for n in base["helpers"]}
    keys |= {("support", s["name"]) for s in setup["support"]}
    return keys | {("global", g["name"]) for g in setup["globals"]}


def sibling_tests(graph, path):
    """Other test files in the same folder, not the base scripts other tests extend."""
    folder = posixpath.dirname(path)
    base_files = {graph.nodes[e.to].file for e in graph.edges.values()
                  if e.kind == "generalization" and is_test(graph.nodes.get(e.from_id))
                  and e.to in graph.nodes}
    return sorted({n.file for n in graph.nodes.values()
                   if n.kind != "external" and n.file != path and posixpath.dirname(n.file) == folder
                   and (is_test(n) or is_test_path(n.file)) and n.file not in base_files})


COMMON_SHARE = 4  # a fixture counts as common when 1/4 of the sibling tests (at least 2) have it


def missing_fixtures(graph, notes, path, setup, index):
    """Fixtures used by several sibling test files that this one lacks, most common first:
    [{kind, name, count, of, example}]."""
    siblings = sibling_tests(graph, path)
    mine, found = fixture_keys(setup), {}
    for sibling in siblings:
        for key in fixture_keys(setup_of(graph, notes, sibling, index, with_missing=False)) - mine:
            row = found.setdefault(key, {"kind": key[0], "name": key[1], "count": 0, "example": sibling})
            row["count"] += 1
    needed = max(min(2, len(siblings)), -(-len(siblings) // COMMON_SHARE))
    return sorted((dict(row, of=len(siblings)) for row in found.values() if row["count"] >= needed),
                  key=lambda r: (-r["count"], r["kind"], r["name"]))


# find: points per matched word by where it matched (best place per word counts), scaled by
# how well the word matched (wordmatch: exact / part of a word / one typo)
NAME_POINTS, EXACT_BONUS, HINT_POINTS, PATH_POINTS, NOTE_POINTS, DOC_POINTS = 10, 5, 5, 3, 2, 1
FUNCTION_NAME_POINTS, FIELD_NAME_POINTS, MEMBER_POINTS = 8, 8, 2
ALL_IN_NAME_BONUS, WHOLE_NAME_BONUS = 10, 20
# one matched word outweighs everything else a word can earn (best is ~15), so hits rank by
# how many words matched first, then by how well
MATCHED_WORD_BONUS = 100
NAMING_POINTS = (NAME_POINTS, FUNCTION_NAME_POINTS)
SNIPPET_MAX = 80


def match_score(words, places, name=None):
    """Per matched word: MATCHED_WORD_BONUS plus its best points x match quality among the
    places (text, points); None when no word matches anywhere. Ranking: more words matched >
    the whole name (or one word equal to it) > every word in the name > words spread over name
    and notes > typos. `name` = the name's text."""
    total, matched, in_name = 0, 0, True
    for word in words:
        best = 0
        for text, points in places:
            quality = text_quality(word, text) if text else 0
            if quality:
                exact = quality == EXACT and points in NAMING_POINTS and text.lower() == word
                best = max(best, points * quality + (EXACT_BONUS if exact else 0))
        if not best:
            in_name = False
            continue
        matched += 1
        total += MATCHED_WORD_BONUS + best
        in_name = in_name and name is not None and text_quality(word, name) >= PART
    if not matched:
        return None
    if name is not None:
        if "".join(split_words(name)) == "".join(words):
            total += WHOLE_NAME_BONUS
        elif in_name:
            total += ALL_IN_NAME_BONUS
    return total


def snippet(text):
    text = " ".join((text or "").split())
    return text if len(text) <= SNIPPET_MAX else text[:SNIPPET_MAX - 1].rstrip() + "…"


def note_places(notes, key):
    """The hint (best), then description, rules and review of a notes key, as match places
    (`values` also holds the nickname, which the caller scores as a name)."""
    values = {name: notes.value(key, name) for name in ("nickname", "hint", "description", "rules", "review")}
    return values, [(values["hint"], HINT_POINTS)] + [
        (values[name], NOTE_POINTS) for name in ("description", "rules", "review")]


def find_hit(unit, kind, file, line, end, score, hint):
    return {"unit": unit, "kind": kind, "file": file, "line": line, "end": end,
            "score": score, "hint": hint}


def find(graph, notes, words, include_tests=False, texts=None):
    """Classes, functions and fields matching any of the words (fuzzy, see wordmatch) in their name,
    path, notes, code doc or linked spec / memo / tour text (`texts`: class id -> [(title, text)]),
    most words matched first, then best match: [{unit, kind, file, line, end, score, hint}]."""
    words, texts = [w.lower() for w in words], texts or {}
    holders = {i.rpartition(".")[0] for i in graph.nodes}
    hits = []
    for node in graph.nodes.values():
        if node.kind == "external" or (is_test(node) and not include_tests):
            continue
        values, places = note_places(notes, node_key(node.id))
        linked = texts.get(node.id, ())
        score = match_score(words, [
            (node.name, NAME_POINTS), (values["nickname"], NAME_POINTS),
            (sole_function(node, holders), NAME_POINTS), (node.id, PATH_POINTS), (node.file, PATH_POINTS), *places,
            (node.doc, DOC_POINTS), *((f"{title} {text}", NOTE_POINTS) for title, text in linked),
            *((m.name, MEMBER_POINTS) for m in node.members if m.kind in FIELD_KINDS)],
            name=node.name)
        if score is not None:
            hint = values["hint"] or snippet(values["description"] or node.doc) \
                or (f"in: {linked[0][0]}" if linked else "")
            hits.append(find_hit((node.id, None), node.kind, node.file, node.line, 0, score, hint))
        for member in node.members:
            is_function = member.kind in FUNCTION_KINDS
            if not is_function and member.kind not in FIELD_KINDS:
                continue
            values, places = note_places(notes, function_key(node.id, member.name)) \
                if is_function else ({"nickname": None, "hint": "", "description": ""}, [])
            score = match_score(words, [
                (member.name, FUNCTION_NAME_POINTS if is_function else FIELD_NAME_POINTS),
                (values["nickname"], FUNCTION_NAME_POINTS),
                (node.name, PATH_POINTS), *places], name=member.name)
            if score is not None:
                hits.append(find_hit((node.id, member.name), member.kind, node.file,
                                     member.line, member.end, score,
                                     values["hint"] or snippet(values["description"])
                                     or message_hint(graph, node, member)))
    return sorted(hits, key=lambda h: (-h["score"], h["unit"][0], h["unit"][1] or ""))


def find_suggestions(graph, words):
    """Class and function names near the words, for a find with no hit."""
    names = {n.name for n in graph.nodes.values() if n.kind != "external" and not is_test(n)}
    names |= {m.name for n in graph.nodes.values() if n.kind != "external" for m in n.members}
    return close_names(words, sorted(names))


def hit_callers(graph, hits):
    """Per hit unit: (callers, tests): who uses it, and the nearest test files using it
    (testcode.tests_using, then the tests of each caller's class), as [(file, via class id)]."""
    incoming = incoming_edges(graph)
    found = {}
    for hit in hits:
        unit = hit["unit"]
        callers = sorted(callers_of(incoming, unit), key=lambda u: (u[0], u[1] or ""))
        tests = [(f, "") for f in tests_using(graph, *unit)]
        for test_file, via in retest_files(graph, unit[0], sorted({c[0] for c in callers})):
            if via and test_file not in dict(tests):
                tests.append((test_file, via))
        found[unit] = (callers, tests)
    return found


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


def unit_key(unit):
    return unit[0], unit[1] or ""


def ripple(graph, seeds, depth):
    """Units depending on the seed units, by distance (each level sorted), up to depth steps."""
    incoming, seen, frontier, levels = incoming_edges(graph), set(seeds), set(seeds), []
    for _ in range(depth):
        frontier = {c for u in frontier for c in callers_of(incoming, u)} - seen
        if not frontier:
            break
        seen |= frontier
        levels.append(sorted(frontier, key=unit_key))
    return levels


def unit_name(graph, unit):
    """The name code and docs write for a unit: the function, else the class (no file type)."""
    node_id, function = unit
    return function or posixpath.splitext(graph.nodes[node_id].name)[0]


def impact(graph, unit, depth=IMPACT_DEPTH, index=None, extra_tests=None):
    """Who depends on a class or `Class#function`: {"levels": [[unit]] by distance (sorted),
    "tests": [(test file, via)] to rerun for it and every dependent found}; with a names index
    (textindex.py) also "mentions": {"test"/"doc": [(file, line)]} naming it.
    extra_tests(unit, test_index) -> test files found some other way (name_reach.command_tests)."""
    levels = ripple(graph, {unit}, depth)
    dependents = sorted({node_id for level in levels for node_id, _ in level} - {unit[0]})
    tests = retest_files(graph, unit[0], dependents)
    if extra_tests:
        known = {f for f, _ in tests}
        tests += [(f, "") for f in extra_tests(unit, index_tests(graph)) if f not in known]
    result = {"levels": levels, "tests": tests}
    if index is not None:
        result["mentions"] = mentions(index, unit_name(graph, unit), MENTION_KINDS)
    return result


BROAD_TESTS = 12  # a fallback of more test files than this is "broad": named, not listed


def changed_units(graph, changed):
    """{(class id, function or None): contract changed} for the non-test files of `changed`
    ({file: [(first, last)]}, last 0 = whole file); the flag is True when a function's def line
    is among the changed lines."""
    units = {}
    for file, ranges in sorted(changed.items()):
        for first, last in ranges:
            lines = range(first, last + 1) if last else ()
            found = {unit_at(graph, file, line): line for line in lines}
            if not last:
                found = {(n.id, None): 0 for n in graph.nodes.values()
                         if n.file == file and n.kind != "external"}
            for unit, line in found.items():
                if unit is None:
                    continue
                node_id, function = unit
                contract = bool(function) and any(
                    m.name == function and first <= m.line <= (last or m.line)
                    for m in graph.nodes[node_id].members)
                units[unit] = units.get(unit, False) or contract
    return units


def diff_tests(graph, changed, extra_tests=None):
    """`tests --diff`: from {file: [(first, last)]} changed lines (filewalk.git_changed_lines) the
    narrowest tests to run. Per changed function: the tests calling it by name, else the tests
    covering its class; lines outside any function: the class's tests. A fallback reaching more
    than BROAD_TESTS files is "broad" (named, not listed). Tests of the callers (by name; their
    direct tests, else their class's unless broad) of a function whose def line changed. Changed test files are listed (via ""). extra_tests(unit, test_index) -> test files found some
    other way (name_reach.command_tests). Returns {"units",
    "tests": [(file, via)], "untested", "broad": [(unit, count)]}."""
    tests = {f: "" for f in changed if is_test_path(f) and is_runnable_test(f)}
    units = changed_units(graph, {f: r for f, r in changed.items() if not is_test_path(f)})
    index, untested, broad = index_tests(graph), [], set()
    for unit, contract in units.items():
        node_id, function = unit
        found = (function and direct_tests(graph, node_id, function)) or covering_tests(graph, node_id, index)
        extra = extra_tests(unit, index) if extra_tests else []
        if len(found) > BROAD_TESTS:
            broad.add(((node_id, None), len(found)))
            found = extra
        else:
            found = sorted({*found, *extra})
            if not found:
                untested.append(unit)
        for test_file in found:
            tests.setdefault(test_file, "")
        if contract:
            for caller, calling in function_callers(graph, node_id, function):
                caller_tests = (calling and direct_tests(graph, caller, calling)) \
                    or covering_tests(graph, caller, index)
                for test_file in (caller_tests if len(caller_tests) <= BROAD_TESTS else ()):
                    tests.setdefault(test_file, caller)
    return {"units": sorted(units, key=unit_key), "untested": sorted(untested, key=unit_key),
            "broad": sorted(broad),
            "tests": sorted(tests.items(), key=lambda item: (item[1] != "", item[0]))}


def name_impact(graph, rows, depth=IMPACT_DEPTH):
    """impact for a name that is not a class (a constant, env var, settings key), from its
    `textindex.uses` rows: the code units whose lines use it (not the line declaring it) at
    distance 1, their dependents further out, the tests covering them or naming it, and the
    docs and tests naming it."""
    direct = {unit_at(graph, r["file"], r["line"]) for r in rows
              if r["kind"] == "src" and not r["declares"]} - {None}
    levels = ([sorted(direct, key=unit_key)] if direct else []) + ripple(graph, direct, depth - 1)
    dependents = sorted({node_id for level in levels for node_id, _ in level})
    found = {kind: [(r["file"], r["line"]) for r in rows if r["kind"] == kind]
             for kind in MENTION_KINDS}
    tests = dict(retest_files(graph, None, dependents))
    for test_file, _ in found["test"]:
        tests.setdefault(test_file, "")
    return {"levels": levels, "mentions": found,
            "tests": sorted(tests.items(), key=lambda item: (item[1] != "", item[0]))}


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


# --- hotspots: size x git churn x missing tests ---

HOTSPOT_KINDS = ("class", "struct", "record", "script", "inner", "module")
TESTED_WEIGHT = 0.25  # a class with covering tests counts a quarter of an untested one


def hotspots(graph, churn):
    """Classes and modules (a module counts its lines outside its classes) ranked worst first: [{"id", "file", "size", "churn", "tested", "score"}].
    churn: {file: commits} (filewalk.git_churn); score = size x churn x (1 untested, else
    TESTED_WEIGHT). Test code, empty modules and classes whose file saw no commit are left out."""
    index = index_tests(graph)
    rows = []
    for node in graph.nodes.values():
        commits = churn.get(node.file, 0)
        if node.kind not in HOTSPOT_KINDS or is_test(node) or not commits or not node.lines:
            continue
        tested = bool(covering_tests(graph, node.id, index))
        score = round(node.lines * commits * (TESTED_WEIGHT if tested else 1))
        rows.append({"id": node.id, "file": node.file, "size": node.lines, "churn": commits,
                     "tested": tested, "score": score})
    return sorted(rows, key=lambda r: (-r["score"], r["id"]))
