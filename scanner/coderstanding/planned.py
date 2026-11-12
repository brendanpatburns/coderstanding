"""Planned and hand-added items, and the one rule for "built" (DESIGN 7f).

Hand-made items live in notes.jsonl, like every hand-written fact, with a `made` field:
- `node:<name>` `made: planned` = a class we mean to build (UI "Planned"); `made: manual` = a
  real thing the scan cannot see (UI "Added manually"; `where` = file, folder or service).
- `node:<class>#<function>` `made: planned` = a planned function row on a real or planned class.
- `edge:A→B` `made: planned|manual` = a planned (dotted) or hand-added (solid) line.
A planned item has its own `stage` / `priority` / `importance` (the spec words, cards.py), or
shows its linked `spec`'s stage. The view engine adds them to its copy of the graph
(`hand_graph`), the way it adds loaded-by lines; nothing here writes graph.json.

"Built" (`is_built`) = not planned and not a stub (`Member.stub`, languages/stubs.py). A planned
function whose class gets a real function of the same name is built: its notes key is that
function's key, so its notes carry over with no copying. A close name (or a planned class whose
name is now real code) is only offered (`matches`, `plan --link`).
"""

import copy
from difflib import SequenceMatcher

from .model import FUNCTION_KINDS, Edge, Member, Node
from .testcode import is_test
from .notes import ARROW, EDGE_PREFIX, FUNCTION_SEP, NODE_PREFIX, edge_key, function_key, node_key
from .wordmatch import split_words, stem

PLANNED, MANUAL = "planned", "manual"
MADE_KINDS = (PLANNED, MANUAL)
MADE_LABELS = {PLANNED: "Planned", MANUAL: "Added manually"}
PLAN_FIELDS = ("made", "stage", "priority", "importance", "spec", "where")  # notes fields of this module
BOX_ONLY_FIELDS = ("made", "stage", "priority", "importance", "where")  # dropped when a box is merged
DEFAULT_STAGE = "idea"  # the spec kind's default stage (cards.SPEC.default_stage)
KEEP_SUFFIX = " (planned)"  # "Keep both": the planned box's new name
CLOSE_RATIO = 0.85  # names this alike (or with the same words) are a close match
BAD_NAME_CHARS = (FUNCTION_SEP, ARROW, ">", "\n")
HAND_FLAG = "hand_made"  # attribute on a graph copy that already holds the hand-made items


class PlanError(ValueError):
    """A refused plan write (the CLI and server show the message)."""


def is_built(item):
    """THE rule for "built": not planned and not a stub. `item` is a model Node or Member, or a
    view row (node or function dict). Added-manually items are real, so built. The planned work
    view and progress tracks read this one function."""
    get = item.get if isinstance(item, dict) else (lambda name: getattr(item, name, None))
    return get("made") != PLANNED and not get("stub")


def check_plan_value(name, value):
    """Raise ValueError for a bad value of a PLAN_FIELDS field (notes.check_value calls this)."""
    from .cards import PRIORITIES, SPEC  # cards imports the view engine, which imports this
    allowed = {"made": MADE_KINDS, "stage": SPEC.stages, "priority": PRIORITIES,
               "importance": PRIORITIES}.get(name)
    if allowed and value not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}, got {value!r}")


# --- reading the notes ---

def split_key(key):
    """("node", id, None) | ("function", id, name) | ("edge", from, to) | None for a notes key."""
    if key.startswith(NODE_PREFIX):
        node_id, sep, name = key[len(NODE_PREFIX):].rpartition(FUNCTION_SEP)
        return ("function", node_id, name) if sep else ("node", name, None)
    if key.startswith(EDGE_PREFIX) and ARROW in key:
        return ("edge", *key[len(EDGE_PREFIX):].split(ARROW, 1))
    return None


def made_keys(notes):
    """{key: made} of every hand-made item in the notes."""
    found = {}
    for key in notes.keys():
        made = notes.value(key, "made")
        if made and split_key(key):
            found[key] = made
    return found


def spec_stage(notes, spec_id):
    """The stage of a spec (its default when unset), None when the spec cannot be read.
    `notes.data_dir` is the app's data folder (apps.App.load_notes sets it)."""
    from .cards import SPEC, card_from_item  # see check_plan_value
    from .items import ItemError, ItemStore
    if not spec_id or not getattr(notes, "data_dir", None):
        return None
    cache = notes.__dict__.setdefault("_spec_stages", {})
    if spec_id not in cache:
        try:
            card = card_from_item(SPEC, ItemStore(notes.data_dir, SPEC.folder).read(spec_id))
            cache[spec_id] = card.stage or SPEC.default_stage
        except (KeyError, ItemError, OSError):
            cache[spec_id] = None
    return cache[spec_id]


def stage_json(notes, key):
    """A planned item's stage (own, else its spec's, else the default: `stage_from` says which),
    priority, importance and spec."""
    stage, spec = notes.value(key, "stage"), notes.value(key, "spec")
    source = "own"
    if stage is None and spec:
        stage, source = spec_stage(notes, spec), "spec"
    if stage is None:
        stage, source = DEFAULT_STAGE, "default"
    return {"stage": stage, "stage_from": source, "priority": notes.value(key, "priority"),
            "importance": notes.value(key, "importance"), "spec": spec}


def author_json(notes, key):
    """Who added a hand-made item (`made_by`) and whether it is still an AI draft (`made_ai`)."""
    _, by = notes.get(key, "made")
    return {"made_by": by, "made_ai": bool(by) and notes.is_ai(key, by)}


# --- matching names against real code ---

def name_words(name):
    return [stem(word) for word in split_words(name)]


def is_close(a, b):
    """Two names a person would call the same: same words (`node_store` / `NodeStore`) or
    nearly the same letters."""
    if a == b:
        return False
    if name_words(a) and name_words(a) == name_words(b):
        return True
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= CLOSE_RATIO


def real_nodes(graph):
    return [n for n in graph.nodes.values() if not n.made and n.kind not in ("external", "domain")]


def counts_as_work(node):
    """Stubs count on the app's own code, not in tests (an empty test helper is not work to do)."""
    return not is_test(node)


def class_matches(graph, name):
    """{"exact": [ids], "close": [ids]} of real classes named like `name` (exact: the id, the
    name, or the last part of a dotted id)."""
    exact, close = [], []
    for node in real_nodes(graph):
        names = {node.id, node.name, node.id.rsplit(".", 1)[-1]}
        if name in names:
            exact.append(node.id)
        elif any(is_close(name, n) for n in names):
            close.append(node.id)
    return {"exact": sorted(exact), "close": sorted(close)}


def function_matches(node, name):
    """{"exact": [names], "close": [names]} of the real functions of a class named like `name`."""
    real = [m.name for m in node.members if m.kind in FUNCTION_KINDS and not m.made] if node else []
    return {"exact": [n for n in real if n == name], "close": [n for n in real if is_close(name, n)]}


# --- the view engine's copy of the graph ---

def hand_node(node_id, made):
    return Node(id=node_id, name=node_id, kind="class", lang="", file="", line=0, level=1,
                span=True, made=made)


def own_copy(nodes, node_id, copied):
    """The node in `nodes`, copied once (its members too) so the saved graph stays untouched."""
    if node_id not in copied:
        node = copy.copy(nodes[node_id])
        node.members = list(node.members)
        nodes[node_id] = node
        copied.add(node_id)
    return nodes[node_id]


def hand_graph(graph, notes, show_planned=True):
    """The graph with the hand-made items added (a shallow copy; the same graph when there are
    none). show_planned False leaves out planned classes, functions and lines (manual ones stay:
    they are real). A planned item whose name is real code now is not added: the real one shows,
    with the same notes key."""
    made = made_keys(notes) if notes else {}
    if not made or getattr(graph, HAND_FLAG, False):
        return graph
    out = copy.copy(graph)
    out.nodes, out.edges = dict(graph.nodes), dict(graph.edges)
    setattr(out, HAND_FLAG, True)
    shown = {key: kind for key, kind in made.items() if show_planned or kind != PLANNED}
    parsed = [(split_key(key), kind) for key, kind in sorted(shown.items())]
    for (what, node_id, _), kind in parsed:
        if what == "node" and node_id not in out.nodes:
            out.nodes[node_id] = hand_node(node_id, kind)
    copied = set()
    for (what, node_id, name), kind in parsed:
        if what == "function" and kind == PLANNED and node_id in out.nodes \
                and not any(m.name == name for m in out.nodes[node_id].members):
            own_copy(out.nodes, node_id, copied).members.append(Member(name, "method", made=PLANNED))
    real_pairs = {(e.from_id, e.to) for e in graph.edges.values()}
    for (what, from_id, to), kind in parsed:
        if what == "edge" and from_id in out.nodes and to in out.nodes \
                and (from_id, to) not in real_pairs:
            out.add_edge(Edge(from_id, to, kind))
    return out


def plan_json(notes, graph, node):
    """The hand-made facts of a view's class row ({} for scanned code): made, its label, where,
    stage etc., author, and `matches` = real classes named like it (built now?)."""
    if not node.made:
        return {}
    key = node_key(node.id)
    data = {"made": node.made, "made_label": MADE_LABELS[node.made], "where": notes.value(key, "where"),
            **author_json(notes, key)}
    if node.made == PLANNED:
        data.update(stage_json(notes, key), matches=class_matches(graph, node.id))
    return data


def function_plan_json(notes, node, member):
    """A function row's marks: `stub`, or `made` planned with its stage etc. and `matches` (real
    functions of the class named like it); {} for a plain function."""
    data = {"stub": True} if member.stub else {}
    key = function_key(node.id, member.name)
    if member.made or (member.stub and notes.value(key, "made")):
        data.update(stage_json(notes, key), **author_json(notes, key))
    if member.made:
        data.update(made=PLANNED, matches=function_matches(node, member.name))
    return data


def plan_counts(graph):
    """How much is not built in a (hand) graph: planned classes and functions, manual classes,
    stubs; the toolbar count and the `graph` header."""
    counts = {"planned": 0, "manual": 0, "functions": 0, "stubs": 0}
    for node in graph.nodes.values():
        if node.made:
            counts[node.made] += 1
        for member in node.members:
            counts["functions"] += member.made == PLANNED
            counts["stubs"] += member.stub and counts_as_work(node)
    return counts


# --- the list (`plan <app>`, GET /api/plan) ---

def plan_rows(graph, notes):
    """Everything not built, for the CLI list and the viewer: {"classes": planned and manual
    classes, "functions": planned functions, "lines": hand-made lines, "stubs": stub functions}.
    A planned function that is real now has `built` (linked automatically); a real stub of that
    name gives it `stub`."""
    rows = {"classes": [], "functions": [], "lines": [], "stubs": []}
    for key, made in made_keys(notes).items():
        what, first, second = split_key(key)
        base = {"key": key, "made": made, "hint": notes.value(key, "hint"), **author_json(notes, key)}
        if what == "node":
            real = first in graph.nodes and not graph.nodes[first].made
            rows["classes"].append({**base, "id": first, "where": notes.value(key, "where"),
                                    **(stage_json(notes, key) if made == PLANNED else {}),
                                    "real": real, "matches": class_matches(graph, first)})
        elif what == "function":
            owner = graph.nodes.get(first)
            member = next((m for m in owner.members if m.name == second and not m.made), None) if owner else None
            rows["functions"].append({**base, "owner": first, "name": second, **stage_json(notes, key),
                                      "owner_made": notes.value(node_key(first), "made"),
                                      "lost": owner is None and not notes.value(node_key(first), "made"),
                                      "built": bool(member) and is_built(member),
                                      "stub": bool(member) and member.stub,
                                      "matches": function_matches(owner, second)})
        else:
            rows["lines"].append({**base, "from": first, "to": second,
                                  "real": any(e.from_id == first and e.to == second for e in graph.edges.values())})
    for node in sorted((n for n in real_nodes(graph) if counts_as_work(n)), key=lambda n: n.id):
        for member in node.members:
            if member.stub:
                key = function_key(node.id, member.name)
                rows["stubs"].append({"key": key, "owner": node.id, "name": member.name, "file": node.file,
                                      "line": member.line, "hint": notes.value(key, "hint")})
    return rows


# --- writes (`plan --add/--drop/--link/--keep-both/--accept`, POST /api/plan...) ---

def check_name(name):
    name = (name or "").strip()
    if not name or any(c in name for c in BAD_NAME_CHARS):
        raise PlanError(f"bad name {name!r}: give a plain name (no {', '.join(BAD_NAME_CHARS[:3])})")
    return name


def hand_ids(graph, notes):
    """Ids of every class: real ones and hand-made ones."""
    return set(graph.nodes) | {split_key(k)[1] for k, m in made_keys(notes).items() if split_key(k)[0] == "node"}


def add_matches(graph, notes, name, on=None):
    """What real code a new item's name matches (the "ask first" rule): {"exact", "close"} or
    None when nothing matches."""
    found = function_matches(graph.nodes.get(on), name) if on else class_matches(graph, name)
    return found if found["exact"] or found["close"] else None


def add_item(graph, notes, name, by, ai, *, on=None, made=PLANNED, fields=None, links=(), anyway=False):
    """Add a planned or manual class, or a planned function `on` a class. Returns {"key"} or,
    when the name matches real code and not `anyway`, {"ask": matches} without writing.
    links: (from, to) pairs of lines to add, one end being the new class."""
    name = check_name(name)
    if made not in MADE_KINDS:
        raise PlanError(f"kind must be one of {', '.join(MADE_KINDS)}")
    if on is not None:
        if made != PLANNED:
            raise PlanError("only planned functions can be added (an added-manually class has none)")
        if on not in hand_ids(graph, notes):
            raise PlanError(f"no class {on}")
        key = function_key(on, name)
    else:
        key = node_key(name)
        if name in graph.nodes and anyway:  # the same id would put the box's notes on the real class
            raise PlanError(f"{name} is a real class's id: write notes on it, or give the box another name")
    if notes.value(key, "made"):
        raise PlanError(f"{key} is already there")
    matches = add_matches(graph, notes, name, on)
    if matches and not anyway:
        return {"key": key, "ask": matches}
    values = {"made": made, **{k: v for k, v in (fields or {}).items() if v not in (None, "", [])}}
    notes.set(key, by, values, ai)
    known = hand_ids(graph, notes)
    for from_id, to in links:
        if from_id not in known or to not in known or name not in (from_id, to):
            raise PlanError(f"line {from_id}>{to}: both ends must be classes, one of them {name}")
        notes.set(edge_key(from_id, to), by, {"made": made}, ai)
    return {"key": key}


def drop_key(notes, key):
    """Remove every author's line of one key."""
    for by in list(notes.authors(key)):
        notes.put(key, by, notes.is_ai(key, by), {name: None for name in list(notes.entries[(key, by)])})


def item_keys(notes, node_id):
    """The node key of a class, its function keys and its line keys (every author's)."""
    keys = []
    for key in notes.keys():
        parsed = split_key(key)
        if parsed and (parsed[1] == node_id or (parsed[0] == "edge" and parsed[2] == node_id)):
            keys.append(key)
    return keys


def drop_item(notes, key):
    """Drop a hand-made item; a class takes its planned functions and hand-made lines with it.
    Returns the dropped keys."""
    if not notes.value(key, "made"):
        raise PlanError(f"{key} is not a planned or added-manually item")
    what, node_id, _ = split_key(key)
    keys = [key] if what != "node" else [k for k in item_keys(notes, node_id)
                                           if k == key or notes.value(k, "made")]
    for each in keys:
        drop_key(notes, each)
    return keys


def renamed_key(key, old_id, new_id):
    what, first, second = split_key(key)
    if what == "edge":
        return edge_key(new_id if first == old_id else first, new_id if second == old_id else second)
    return function_key(new_id, second) if what == "function" else node_key(new_id)


def move_key(notes, old, new, drop=()):
    """Move every author's fields from one key to another; a field the new key already has is
    kept (the real code's notes win); `drop` fields are left behind (removed)."""
    for by in list(notes.authors(old)):
        ai = notes.is_ai(old, by)
        fields = {name: value for name, value in notes.entries[(old, by)].items()
                  if name not in drop and notes.get(new, name)[0] is None}
        if fields:
            notes.put(new, by, ai, fields)
    drop_key(notes, old)


def link_item(graph, notes, old, new):
    """Link a planned item to the real code it became: `old`/`new` are both classes (the box's
    notes, functions and lines move to the real class; its stage, priorities and kind are
    dropped) or both `Class#function` (the planned row's notes move to the real function).
    Returns the moved keys as (old, new)."""
    old_kind, new_kind = split_key(node_key(old))[0], split_key(node_key(new))[0]
    if old_kind != new_kind:
        raise PlanError("link a class to a class, or Class#function to Class#function")
    if not notes.value(node_key(old), "made"):
        raise PlanError(f"{old} is not planned")
    if old_kind == "function":
        owner, name = new.rsplit(FUNCTION_SEP, 1)
        if not any(m.name == name and not m.made for m in getattr(graph.nodes.get(owner), "members", ())):
            raise PlanError(f"{owner} has no function {name}")
        move_key(notes, node_key(old), node_key(new), drop=("made",))
        return [(node_key(old), node_key(new))]
    if new not in graph.nodes or graph.nodes[new].made:
        raise PlanError(f"no real class {new}")
    return rename_item(notes, old, new, BOX_ONLY_FIELDS)


def rename_item(notes, old_id, new_id, drop=()):
    """Move a class's keys (node, functions, lines) to a new id; `drop` fields leave its node key."""
    moved = []
    for key in item_keys(notes, old_id):
        new = renamed_key(key, old_id, new_id)
        move_key(notes, key, new, drop if key == node_key(old_id) else ())
        moved.append((key, new))
    return moved


def keep_both(notes, node_id):
    """"Keep both": rename a planned class whose name is now real code to `<name> (planned)`."""
    if notes.value(node_key(node_id), "made") != PLANNED:
        raise PlanError(f"{node_id} is not a planned class")
    return rename_item(notes, node_id, node_id + KEEP_SUFFIX)


def accept_item(notes, key, by):
    """Make every AI-draft field of a hand-made item (and of its lines) `by`'s. Returns the keys."""
    what, node_id, _ = split_key(key)
    keys = [key] + ([k for k in item_keys(notes, node_id) if k != key and split_key(k)[0] == "edge"]
                    if what == "node" else [])
    for each in keys:
        for author in list(notes.authors(each)):
            if notes.is_ai(each, author):
                fields = {n: v for n, v in notes.entries[(each, author)].items() if n != "fp"}
                notes.set(each, by, fields, ai=False)
    return keys
