"""Warnings per node (docs/DESIGN.md 12a), computed once after the scan resolves.

Kinds, stored on `node.warnings` as small dicts:
- `unused`: nothing outside tests points at it (entry points, autoloads, tests and outside
  code are never unused).
- `god`: far more neighbours than the repo's norm: degree >= max(GOD_MIN, GOD_FACTOR x median).
- `cycle`: part of a dependency cycle (strongly connected set > 1 over non-inheritance lines).
  `group` names the cycle by its smallest id, `size` counts its members.
- `unsure`: lines whose target name matched several classes (first match taken).
- `unresolved`: outside references that are no known package, engine API or standard library.
- Godot loose ends (`names` = the signals / handlers, `count` = how many; hints, since
  dynamic connects and string signal names are not seen): `signal-unconnected` (emitted, never
  connected), `signal-unemitted` (connected, never emitted), `bad-handler` (a connection names a
  function the script and its base scripts lack), `autoload-unused` (no script uses the autoload).

`stale` is a separate list (not a node warning): notes whose code changed (notes.stale_notes).

Degree and cycles ignore tests, outside code, and lines between a type and the types nested
in it (a module and its classes, a script and its inner classes): those are containment.
"""

import re
from collections import Counter
from statistics import median

from .model import ANY_SIGNAL, EDGE_KINDS, EMIT_DETAIL, SIGNAL_ARROW, STRUCT_KINDS
from .stack import is_unresolved
from .testcode import is_test

GOD_MIN = 20  # never call a class with fewer neighbours than this a god class
GOD_FACTOR = 6  # ... nor one below this many times the median degree
ALWAYS_USED_FLAGS = ("entry", "autoload", "test")
WARNING_HELP = {  # one line each, shown by the viewer
    "unused": "Nothing outside tests points at it",
    "god": "Far more connections than the repo's norm",
    "cycle": "Part of a dependency cycle (number = classes in it)",
    "unsure": "Lines whose target name matched several classes",
    "unresolved": "Outside references that are no known package, engine API or standard library",
    "layer": "A line that breaks an architecture rule (`rules` command)",
    "signal-unconnected": "Signal emitted but never connected. A hint: dynamic connects, "
                          "string-named signals and connects from untyped nodes are not seen",
    "signal-unemitted": "Signal connected but never emitted. A hint: dynamic emits, "
                        "string-named signals and emits from untyped nodes are not seen",
    "bad-handler": "A connection names a handler (_on_..) its script and base scripts lack. A hint: "
                   "only handler-style names are checked; functions added at runtime are not seen",
    "autoload-unused": "No script uses this autoload. A hint: it may work on its own, or be "
                       "reached by get_node(\"/root/..\")",
}
WARNING_KINDS = tuple(WARNING_HELP)
STALE_KIND = "stale"  # a note list, not a node warning: see warnings_by_kind


def is_nested(a, b):
    return a.startswith(b + ".") or b.startswith(a + ".")


def checked_ids(graph):
    """In-repo, non-test nodes: the ones that get warnings."""
    return {i for i, n in graph.nodes.items() if n.kind != "external" and not is_test(n)}


def neighbours(graph, ids):
    """id -> set of linked ids (both directions), containment lines left out."""
    found = {i: set() for i in ids}
    for edge in graph.edges.values():
        a, b = edge.from_id, edge.to
        if a in ids and b in ids and a != b and not is_nested(a, b):
            found[a].add(b)
            found[b].add(a)
    return found


def god_threshold(degrees):
    return max(GOD_MIN, round(GOD_FACTOR * median(degrees))) if degrees else GOD_MIN


def used_ids(graph):
    """Ids something outside tests points at."""
    return {e.to for e in graph.edges.values()
            if e.from_id != e.to and not is_test(graph.nodes.get(e.from_id))}


def unused_ids(graph, ids):
    used = used_ids(graph)
    return {i for i in ids if i not in used
            and not set(graph.nodes[i].flags) & set(ALWAYS_USED_FLAGS)}


def cycle_groups(graph, ids):
    """Strongly connected sets of more than one node (iterative Tarjan)."""
    out = {i: set() for i in ids}
    for edge in graph.edges.values():
        if edge.kind not in STRUCT_KINDS and edge.from_id in ids and edge.to in ids \
                and edge.from_id != edge.to and not is_nested(edge.from_id, edge.to):
            out[edge.from_id].add(edge.to)
    return [group for group in strongly_connected(out) if len(group) > 1]


def strongly_connected(out):
    index, low, on_stack, stack, groups = {}, {}, set(), [], []
    for start in sorted(out):
        if start in index:
            continue
        work = [(start, iter(sorted(out[start])))]
        index[start] = low[start] = len(index)
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is None:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    group = set()
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        group.add(member)
                        if member == node:
                            break
                    groups.append(group)
            elif child not in index:
                index[child] = low[child] = len(index)
                stack.append(child)
                on_stack.add(child)
                work.append((child, iter(sorted(out[child]))))
            elif child in on_stack:
                low[node] = min(low[node], index[child])
    return groups


def count_unresolved(node):
    return sum(len(e.at) for e in node.external_refs.values() if is_unresolved(node, e.to))


# --- Godot loose ends: signals nobody connects / emits, handlers that do not exist, autoloads ---

GODOT_LANGS = ("gdscript", "csharp")
SIGNAL_MEMBER = "signal"
HANDLER_PREFIXES = ("_on", "on_", "_handle", "handle_")  # engine functions are unknown, so only
#                                                          handler-style names count as missing


def all_hooks(graph):
    """(declaring node, edge) for every edge, including outside ones (engine types)."""
    for edge in graph.edges.values():
        yield graph.nodes[edge.from_id], edge
    for node in graph.nodes.values():
        for edge in node.external_refs.values():
            yield node, edge


def lineage(bases, node_id):
    """The node and its base classes (in-repo ones), nearest first."""
    order, todo = [], [node_id]
    while todo:
        found = todo.pop(0)
        if found not in order:
            order.append(found)
            todo += bases.get(found, [])
    return order


def base_classes(graph):
    bases = {}
    for edge in graph.edges.values():
        if edge.kind in STRUCT_KINDS:
            bases.setdefault(edge.from_id, []).append(edge.to)
    return bases


class SignalUse:
    """Which signal names are emitted / connected on each class, from the graph's edges.

    A name seen on a target that is no class of the repo (a scene, an engine type) counts for
    every class (`anywhere`); an ANY_SIGNAL name (not a literal) counts for every signal of the
    class. A signal counts for a class's base classes and subclasses alike."""

    def __init__(self, graph):
        bases = base_classes(graph)
        self.family = {}  # id -> ids of it and everything inheriting from it
        for node_id in graph.nodes:
            for base in lineage(bases, node_id):
                self.family.setdefault(base, set()).add(node_id)
        self.emitted, self.connected = {}, {}  # class id -> signal names
        self.anywhere = {"emitted": set(), "connected": set()}
        for _, edge in all_hooks(graph):
            self.add(graph, edge)

    def add(self, graph, edge):
        """Emit edges name signals in their calls, signal edges in their detail; other edges'
        calls on a signal name (`await other.died`) count as using it."""
        if EMIT_DETAIL in edge.detail:
            side, names = "emitted", {c.member for c in edge.calls}
        elif edge.kind == "signal":
            side, names = "connected", set(edge.detail)
        else:
            side, names = "connected", {c.member for c in edge.calls}
        target = graph.nodes.get(edge.to)
        if target is None and side == "connected" and edge.kind != "signal":
            return  # an ordinary call on an outside type says nothing about our signals
        if target is None or target.kind == "scene":
            self.anywhere[side] |= names
        else:
            getattr(self, side).setdefault(edge.to, set()).update(names)

    def seen(self, side, class_id, signal):
        found = getattr(self, side)
        return signal in self.anywhere[side] or any(
            signal in found.get(other, ()) or ANY_SIGNAL in found.get(other, ())
            for other in self.family.get(class_id, ()))


def declared_signals(node):
    return sorted({m.name for m in node.members if m.kind == SIGNAL_MEMBER})


def signal_warnings(graph, ids):
    """(node id, kind, names) for signals emitted but not connected, or connected but not emitted."""
    use = SignalUse(graph)
    for node_id in sorted(ids):
        node = graph.nodes[node_id]
        if node.lang not in GODOT_LANGS:
            continue
        signals = declared_signals(node)
        yield (node_id, "signal-unconnected",
               [s for s in signals if use.seen("emitted", node_id, s)
                and not use.seen("connected", node_id, s)])
        yield (node_id, "signal-unemitted",
               [s for s in signals if use.seen("connected", node_id, s)
                and not use.seen("emitted", node_id, s)])


def handler_warnings(graph, ids):
    """(node id, "bad-handler", [signal -> handler]) for connect()s in GDScript naming a
    function neither the script nor its base scripts have."""
    bases = base_classes(graph)
    missing = {}
    for owner, edge in all_hooks(graph):
        if edge.kind != "signal" or owner.lang != "gdscript" or owner.id not in ids:
            continue
        known = {m.name for base in lineage(bases, owner.id) if base in graph.nodes
                 for m in graph.nodes[base].members}
        for call in edge.calls:
            signal, _, handler = call.member.partition(SIGNAL_ARROW)
            if (handler.startswith(HANDLER_PREFIXES) and "." not in handler
                    and handler not in known):
                missing.setdefault(owner.id, set()).add(f"{signal}{SIGNAL_ARROW}{handler}")
    for node_id, names in sorted(missing.items()):
        yield node_id, "bad-handler", sorted(names)


def autoload_warnings(graph, ids):
    used = used_ids(graph)
    return [(i, "autoload-unused", []) for i in sorted(ids)
            if "autoload" in graph.nodes[i].flags and i not in used]


def add_godot_warnings(graph, ids):
    found = [*signal_warnings(graph, ids), *handler_warnings(graph, ids),
             *autoload_warnings(graph, ids)]
    for node_id, kind, names in found:
        if kind == "autoload-unused":
            graph.nodes[node_id].warnings.append({"kind": kind})
        elif names:
            graph.nodes[node_id].warnings.append({"kind": kind, "names": names,
                                                  "count": len(names)})


def check_graph(graph, layers=(), notes=None):
    """Set `warnings` on every node (tests and outside nodes get none). `layers` (architecture
    rules) need the notes' domains and tags; the settings are not part of a scan, so the
    loaders call `apply_note_warnings` again when they have both."""
    ids = checked_ids(graph)
    for node in graph.nodes.values():
        node.warnings = []
    for node_id in sorted(unused_ids(graph, ids)):
        graph.nodes[node_id].warnings.append({"kind": "unused"})
    links = neighbours(graph, ids)
    degrees = {i: len(n) for i, n in links.items()}
    threshold = god_threshold(list(degrees.values()))
    for node_id, degree in degrees.items():
        if degree >= threshold:
            graph.nodes[node_id].warnings.append(
                {"kind": "god", "degree": degree, "threshold": threshold})
    for group in cycle_groups(graph, ids):
        for node_id in group:
            graph.nodes[node_id].warnings.append(
                {"kind": "cycle", "group": min(group), "size": len(group)})
    add_godot_warnings(graph, ids)
    unsure = Counter(e.from_id for e in graph.edges.values() if e.unsure)
    for node_id in ids:
        node = graph.nodes[node_id]
        for kind, count in (("unsure", unsure[node_id]),
                            ("unresolved", count_unresolved(node))):
            if count:
                node.warnings.append({"kind": kind, "count": count})
    apply_note_warnings(graph, layers, notes)


MAX_NAMES_SHOWN = 3


def names_text(names):
    shown = ", ".join(names[:MAX_NAMES_SHOWN])
    return shown + (f" +{len(names) - MAX_NAMES_SHOWN}" if len(names) > MAX_NAMES_SHOWN else "")


def warning_text(warning):
    """Short text for one warning: `unused`, `god 34`, `cycle 5`, `signal-unemitted hit, died`."""
    kind = warning["kind"]
    if kind == "layer":
        return (f"layer: {warning['to_name']} ({warning['to_file']}) via {warning['edge']} "
                f"at {warning['file']}:{warning['line']}")
    if warning.get("names"):
        return f"{kind} {names_text(warning['names'])}"
    number = warning.get("degree") or warning.get("size") or warning.get("count")
    return f"{kind} {number}" if number else kind


ALL = "all"  # in a rule's `ignore`: every kind
RULE_FILTERS = ("lang", "kind")  # node fields a rule may also match on


def parse_rule(glob, ignore, **filters):
    """A warning rule as stored: {glob, ignore: [kinds], lang?, kind?}. ValueError if bad."""
    kinds = list(ignore)
    if not glob or not kinds or not all(k in WARNING_KINDS or k == ALL for k in kinds):
        raise ValueError(f"a rule needs a folder glob and ignore= some of "
                         f"{', '.join(WARNING_KINDS)}, {ALL}")
    unknown = set(filters) - set(RULE_FILTERS)
    if unknown:
        raise ValueError(f"unknown rule filter {', '.join(sorted(unknown))} "
                         f"(known: {', '.join(RULE_FILTERS)})")
    return {"glob": glob, "ignore": kinds, **{k: v for k, v in filters.items() if v}}


def glob_regex(glob):
    """Repo-relative glob: `*` stays in a folder, `**` crosses folders, `?` one character."""
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif glob.startswith("**", i):
            out, i = out + ".*", i + 2
        else:
            out += "[^/]*" if glob[i] == "*" else "[^/]" if glob[i] == "?" else re.escape(glob[i])
            i += 1
    return re.compile(out + "$")


def parse_loaded_rule(loaded_by, files, how=""):
    """An app-level "loaded by" rule as stored: {loaded_by: class id, files: glob, how?}. Every
    class in a matching file is loaded by that class (a plugin registry). ValueError if bad."""
    if not loaded_by or not files:
        raise ValueError("a loaded-by rule needs a loader class id and a files glob")
    return {"loaded_by": loaded_by, "files": files, **({"how": how} if how else {})}


def rule_matches(rule, node):
    return (glob_regex(rule["glob"]).match(node.file or "") is not None
            and all(rule.get(f) in (None, getattr(node, f)) for f in RULE_FILTERS))


def ignore_reason(kind, node, note_kinds, rules):
    """Why a warning is ignored ("note", "rule tools/** unresolved"), or None if it shows.
    The one place that decides this; notes win over rules."""
    if kind in note_kinds:
        return "note"
    for rule in rules:
        if (kind in rule["ignore"] or ALL in rule["ignore"]) and rule_matches(rule, node):
            return f"rule {rule['glob']} {ALL if ALL in rule['ignore'] else kind}"
    return None


def split_ignored(node, notes=None):
    """(shown, ignored) warnings of a node; each ignored one carries its `reason`."""
    note_kinds = notes.ignored_warnings(node.id) if notes else []
    rules = notes.rules if notes else []
    shown, ignored = [], []
    for warning in node.warnings:
        reason = ignore_reason(warning["kind"], node, note_kinds, rules)
        if reason:
            ignored.append({**warning, "reason": reason})
        else:
            shown.append(warning)
    return shown, ignored


def drop_broken_cycles(items):
    """Cycle warnings whose cycle lost members to ignoring: a cycle needs 2+ members left."""
    members = Counter(w["group"] for _, w in items)
    return [(i, w) for i, w in items if members[w["group"]] >= 2]


def warnings_by_kind(graph, notes=None, show_ignored=False):
    """kind -> [(node id, warning)] in WARNING_KINDS order; biggest first, then by id.
    Warnings a note or rule ignores are left out, or kept with `ignored: True` (show_ignored)."""
    groups = {kind: [] for kind in WARNING_KINDS}
    for node_id, node in sorted(graph.nodes.items()):
        shown, ignored = split_ignored(node, notes)
        for warning in shown + ([{**w, "ignored": True} for w in ignored] if show_ignored else []):
            groups.setdefault(warning["kind"], []).append((node_id, warning))
    if not show_ignored:
        groups["cycle"] = drop_broken_cycles(groups["cycle"])
    stale = notes.stale_notes(graph) if notes else []
    if stale:  # not a node warning (and not ignorable): the notes whose code changed
        groups[STALE_KIND] = [(key, {"kind": STALE_KIND, "fields": fields}) for key, fields in stale]
    for items in groups.values():
        items.sort(key=lambda item: -(item[1].get("degree") or item[1].get("count") or 0))
    return groups


# --- architecture rules (docs/DESIGN.md "Architecture rules"): settings `layers` ---

LAYER_MODES = ("forbid", "only")
LAYER_KINDS = EDGE_KINDS + ("call", "inheritance")  # `call`: a line with calls; `inheritance`: bases
TAG_PREFIX = "tag:"
DOMAIN_PREFIX = "domain:"  # also the view id prefix of a domain card (notes.py uses this)


def layer_label(layer):
    """`src/ui forbid src/net kind=call` (the text `rules` lists and warnings carry)."""
    kind = f" kind={layer['kind']}" if layer.get("kind") else ""
    return f"{layer['from']} {layer['mode']} {layer['to']}{kind}"


def parse_layer(from_, to, mode, kind=None, why=None):
    """An architecture rule as stored: {from, to, mode, kind?, why?}. ValueError if bad."""
    if not from_ or not to or mode not in LAYER_MODES:
        raise ValueError(f"a layer rule needs FROM, TO and a mode: {' or '.join(LAYER_MODES)}")
    if kind and kind not in LAYER_KINDS:
        raise ValueError(f"unknown edge kind {kind} (known: {', '.join(LAYER_KINDS)})")
    extra = {"kind": kind, "why": why}
    return {"from": from_, "to": to, "mode": mode, **{k: v for k, v in extra.items() if v}}


def side_matcher(spec, notes):
    """node -> bool for a rule side: `domain:<name>`, `tag:<tag>` or a folder glob."""
    if spec.startswith(DOMAIN_PREFIX):
        name = spec[len(DOMAIN_PREFIX):]
        return lambda node: notes is not None and notes.domain_of(node.id) == name
    if spec.startswith(TAG_PREFIX):
        tag = spec[len(TAG_PREFIX):]
        return lambda node: notes is not None and tag in notes.tags_of(node.id)
    regex = glob_regex(spec)
    return lambda node: regex.match(node.file or "") is not None


def check_layer_sides(layer, graph, notes):
    """ValueError if a side names an unknown domain or tag, or matches no class."""
    for spec in (layer["from"], layer["to"]):
        if spec.startswith(DOMAIN_PREFIX) and spec[len(DOMAIN_PREFIX):] not in notes.domains():
            raise ValueError(f"unknown domain {spec[len(DOMAIN_PREFIX):]}")
        if spec.startswith(TAG_PREFIX) and spec[len(TAG_PREFIX):] not in notes.tags():
            raise ValueError(f"unknown tag {spec[len(TAG_PREFIX):]}")
        match = side_matcher(spec, notes)
        if not any(match(n) for n in graph.nodes.values()):
            raise ValueError(f"{spec} matches no class")


def edge_is_kind(edge, kind):
    if kind == "call":
        return bool(edge.calls)
    return edge.kind in STRUCT_KINDS if kind == "inheritance" else edge.kind == kind


def names_tests(layer):
    """Tests count on the `from` side only when the rule's glob mentions them."""
    return "test" in layer["from"].lower() and ":" not in layer["from"]


def breaks_layer(layer, source, target, from_match, to_match):
    if not to_match(target):
        return False
    inside = from_match(source)
    return inside if layer["mode"] == "forbid" else not inside


def layer_warnings(graph, layers, notes):
    """node id -> [`layer` warning], one per real line that breaks a rule. The warning carries
    the line (`to`, `edge` kind, `file:line`) and the rule (`rule` text, `why`)."""
    found = {}
    matchers = [(layer, side_matcher(layer["from"], notes), side_matcher(layer["to"], notes))
                for layer in layers]
    for edge in graph.edges.values():
        source, target = graph.nodes.get(edge.from_id), graph.nodes.get(edge.to)
        if not source or not target or source.kind == "external" or edge.from_id == edge.to \
                or is_nested(edge.from_id, edge.to):
            continue
        for layer, from_match, to_match in matchers:
            if (is_test(source) and not names_tests(layer)) \
                    or (layer.get("kind") and not edge_is_kind(edge, layer["kind"])) \
                    or not breaks_layer(layer, source, target, from_match, to_match):
                continue
            where = edge.at[0] if edge.at else None
            found.setdefault(edge.from_id, []).append({
                "kind": "layer", "to": edge.to, "to_name": target.name, "to_file": target.file,
                "edge": edge.kind, "file": where.file if where else source.file,
                "line": where.line if where else source.line,
                "rule": layer_label(layer), "why": layer.get("why", "")})
    return found


def apply_note_warnings(graph, layers, notes):
    """(Re)set the warnings that depend on the notes and settings, not on the scan: `layer`
    (architecture rules) and `unused` (a class something loads by name is not unused). Safe to
    call again with other rules."""
    found = layer_warnings(graph, layers, notes) if layers else {}
    loaded = notes.loaders(graph) if notes else {}
    unused = unused_ids(graph, checked_ids(graph)) - set(loaded)
    for node_id, node in graph.nodes.items():
        kept = [w for w in node.warnings if w["kind"] not in ("layer", "unused")]
        first = [{"kind": "unused"}] if node_id in unused else []
        node.warnings = first + kept + found.get(node_id, [])
