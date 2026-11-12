"""Progress tracks: generic "how much is done" numbers for any project (DESIGN 7g).

A track = a name + "of" (the set of classes or functions it counts, picked with the map's
filters) + "done" (what counts as done for one of them). Projects define their own in
settings.json `tracks`; three built-in ones need no setup (Described, Tested, Built). A setting
with a built-in name replaces it.

    {"name": "Proven", "of": {"files": "game/**", "units": "functions"}, "done": {"tag": "proven"}}

`of` (every key optional, all must match): `units` classes (default) | functions, `files` (a
repo glob), `domain`, `tag`, `file_kind` (one or a list; default: code and scenes, not
supporting files), `lang`, `tests` (true: count test code too; default false).
`done` (exactly one): `{"tag": T}`, `{"field": F}` (the note field is filled; `"by": "person"`:
only a person's value counts, not an AI draft), `{"fact": F}` with F one of FACTS.

Project truth stays outside: a project's own script tags items through `set --file`; no project
formats live here. This module is the one place that computes a track's numbers: the CLI
(`progress`), the API (/api/progress) and the view engine (rings per box, bars per domain) use it.
History per scan is in HISTORY_FILE beside graph.json (one JSON line per scan, keyed by the
graph.json file's mtime in ns: `scan`, so two scans in one second stay apart).
"""

import json
from dataclasses import dataclass

from .checks import DOMAIN_PREFIX, TAG_PREFIX, side_matcher, split_ignored
from .filekinds import is_supporting_file
from .model import FUNCTION_KINDS
from .notes import FUNCTION_SEP, node_key
from .planned import hand_graph, is_built
from .testcode import covering_tests, function_test_index, index_tests, is_test

CLASSES, FUNCTIONS = "classes", "functions"
UNIT_KINDS = (CLASSES, FUNCTIONS)
FACTS = {"tested": "a test reaches it (a class: a test file has a line to it; a function: a test calls it)",
         "built": "not planned and not a stub (planned.is_built)",
         "no_warnings": "no shown warnings (a function: its class's)"}
PERSON = "person"
OF_KEYS = ("units", "files", "domain", "tag", "file_kind", "lang", "tests")
DONE_KEYS = ("tag", "field", "fact")
BUILT_IN = (
    {"name": "Described", "of": {}, "done": {"field": "hint"}},
    {"name": "Tested", "of": {}, "done": {"fact": "tested"}},
    {"name": "Built", "of": {"units": FUNCTIONS}, "done": {"fact": "built"}},
)
HISTORY_FILE = "progress.jsonl"
HISTORY_KEEP = 200  # scans kept in the history file


# --- track definitions (settings.json `tracks`) ---

def check_track(raw):
    """A track as stored, validated: {name, of, done}. ValueError says what is wrong."""
    if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
        raise ValueError("a track needs a name")
    name = str(raw["name"]).strip()
    of, done = raw.get("of") or {}, raw.get("done") or {}
    if not isinstance(of, dict) or not isinstance(done, dict):
        raise ValueError(f"track {name}: `of` and `done` must be objects")
    unknown = sorted(set(of) - set(OF_KEYS))
    if unknown:
        raise ValueError(f"track {name}: unknown `of` key {', '.join(unknown)} (known: {', '.join(OF_KEYS)})")
    if of.get("units", CLASSES) not in UNIT_KINDS:
        raise ValueError(f"track {name}: units must be {' or '.join(UNIT_KINDS)}")
    rules = [key for key in DONE_KEYS if done.get(key)]
    if len(rules) != 1:
        raise ValueError(f"track {name}: `done` needs exactly one of {', '.join(DONE_KEYS)}")
    if done.get("fact") and done["fact"] not in FACTS:
        raise ValueError(f"track {name}: unknown fact {done['fact']} (known: {', '.join(FACTS)})")
    if done.get("by") not in (None, PERSON) or (done.get("by") and not done.get("field")):
        raise ValueError(f"track {name}: `by` can only be \"{PERSON}\", with a field")
    return {"name": name, "of": dict(of), "done": dict(done)}


def app_tracks(settings_tracks):
    """(tracks, problems): the built-in tracks, replaced or followed by the app's own (in their
    order); a bad setting is left out and listed, never fatal."""
    tracks, problems = {t["name"].lower(): {**t, "builtin": True} for t in BUILT_IN}, []
    for number, raw in enumerate(settings_tracks or (), 1):
        try:
            track = check_track(raw)
        except ValueError as error:
            problems.append(f"track {number}: {error}")
            continue
        tracks.pop(track["name"].lower(), None)
        tracks[track["name"].lower()] = {**track, "builtin": False}
    return list(tracks.values()), problems


def find_track(tracks, name):
    """The track called `name` (any case), or None."""
    return next((t for t in tracks if t["name"].lower() == (name or "").strip().lower()), None)


def of_text(track):
    """`functions in game/** with tag x` (what the track counts, for the text output)."""
    of = track["of"]
    parts = [of.get("units", CLASSES)]
    words = {"files": "in", "domain": "in domain", "tag": "tagged", "file_kind": "of kind", "lang": "in"}
    parts += [f"{words[key]} {','.join(of[key]) if isinstance(of[key], list) else of[key]}"
              for key in words if of.get(key)]
    return " ".join(parts + (["incl. tests"] if of.get("tests") else []))


def done_text(track):
    done = track["done"]
    if done.get("tag"):
        return f"tag {done['tag']}"
    if done.get("field"):
        return f"{done['field']} filled" + (" by a person" if done.get("by") else "")
    return done["fact"].replace("_", " ")


# --- units: what a track counts ---

@dataclass
class Unit:
    """One counted item: a class (member None) or a function of it. `id` is its view id."""
    id: str
    node: object
    member: object = None


def as_list(value):
    return value if isinstance(value, list) else [value]


def node_matcher(of, notes):
    """node -> bool for a track's `of` (the class filters; reuses the architecture rules' sides)."""
    tests, kinds, langs = bool(of.get("tests")), of.get("file_kind"), of.get("lang")
    sides = [side_matcher(spec, notes) for spec in (
        of.get("files"), of.get("domain") and DOMAIN_PREFIX + of["domain"],
        of.get("tag") and TAG_PREFIX + of["tag"]) if spec]

    def matches(node):
        if node.kind in ("external", "domain") or (is_test(node) and not tests):
            return False
        if kinds and node.file_kind not in as_list(kinds):
            return False
        if not kinds and is_supporting_file(node):
            return False
        if langs and node.lang not in as_list(langs):
            return False
        return all(side(node) for side in sides)
    return matches


def node_units(node, units):
    if units == CLASSES:
        return [Unit(node.id, node)]
    return [Unit(f"{node.id}{FUNCTION_SEP}{m.name}", node, m) for m in node.members if m.kind in FUNCTION_KINDS]


def track_units(graph, notes, track, node_ids=None):
    """Every unit the track counts (in a hand graph, so planned items count as not built);
    `node_ids` narrows it to those classes (a box, a spec's links)."""
    matches, units = node_matcher(track["of"], notes), track["of"].get("units", CLASSES)
    nodes = graph.nodes.values() if node_ids is None else (graph.nodes[i] for i in node_ids if i in graph.nodes)
    return [unit for node in nodes if matches(node) for unit in node_units(node, units)]


class Judge:
    """Decides "done" for units of one graph; builds the test indexes once, when first needed."""

    def __init__(self, graph, notes):
        self.graph, self.notes = graph, notes
        self._class_tests = self._function_tests = None

    def tested(self, unit):
        if unit.member is None:
            if self._class_tests is None:
                self._class_tests = index_tests(self.graph)
            return bool(covering_tests(self.graph, unit.node.id, self._class_tests))
        if self._function_tests is None:
            self._function_tests = function_test_index(self.graph)
        return bool(self._function_tests.get((unit.node.id, unit.member.name)))

    def fact(self, name, unit):
        if name == "tested":
            return self.tested(unit)
        if name == "built":
            return is_built(unit.member if unit.member is not None else unit.node)
        return not split_ignored(unit.node, self.notes)[0]  # no_warnings

    def done(self, track, unit):
        rule = track["done"]
        if rule.get("tag"):
            return rule["tag"] in self.notes.tags_of(unit.id)
        if rule.get("field"):
            key = node_key(unit.id)
            return self.notes.human_has(key, rule["field"]) if rule.get("by") else self.notes.has(key, rule["field"])
        return self.fact(rule["fact"], unit)


# --- numbers ---

def score(done, total):
    return {"done": done, "total": total, "percent": round(100 * done / total, 1) if total else None}


def count(judge, track, units):
    return score(sum(judge.done(track, u) for u in units), len(units))


class Progress:
    """The numbers of every track of one app: graph (made a hand graph here) + notes (their
    `tracks` setting, set by apps.load_notes)."""

    def __init__(self, graph, notes):
        self.graph, self.notes = hand_graph(graph, notes), notes
        self.tracks, self.problems = app_tracks(getattr(notes, "tracks", ()))
        self.judge = Judge(self.graph, notes)

    def track(self, name):
        return find_track(self.tracks, name)

    def numbers(self, track, node_ids=None):
        return count(self.judge, track, track_units(self.graph, self.notes, track, node_ids))

    def summary(self):
        """[{name, of, done_rule, builtin, done, total, percent}] of every track."""
        return [{"name": t["name"], "of": of_text(t), "done_rule": done_text(t), "builtin": t["builtin"],
                 **self.numbers(t)} for t in self.tracks]

    def by_domain(self, track):
        """[{domain, done, total, percent}] per domain (None = no domain), largest first."""
        groups = {}
        for unit in track_units(self.graph, self.notes, track):
            groups.setdefault(self.notes.domain_of(unit.node), []).append(unit)
        rows = [{"domain": name, **count(self.judge, track, units)} for name, units in groups.items()]
        return sorted(rows, key=lambda r: (r["domain"] is None, -r["total"], r["domain"] or ""))

    def todo(self, track):
        """View ids of the units not done yet, sorted."""
        return sorted(u.id for u in track_units(self.graph, self.notes, track) if not self.judge.done(track, u))

    def of_links(self, track, targets):
        """Numbers over linked targets (view ids `A`, `A#f`; lines ignored): a class link counts
        its functions on a functions track, a function link counts its class on a classes track.
        Only units the track counts at all are kept (its `of`)."""
        wanted = set()
        for target in targets:
            owner, _, function = target.partition(FUNCTION_SEP)
            if owner in self.graph.nodes:
                wanted.add(target if function and track["of"].get("units") == FUNCTIONS else owner)
        units = track_units(self.graph, self.notes, track, sorted({w.partition(FUNCTION_SEP)[0] for w in wanted}))
        return count(self.judge, track, [u for u in units if u.id in wanted or u.node.id in wanted])

    def links_summary(self, targets):
        """summary() over linked targets (a spec's, Cards.tree_focus): no delta, no history."""
        return [{**row, **self.of_links(self.track(row["name"]), targets)} for row in self.summary()]

    def json(self, name=None, history=(), scan=None):
        """/api/progress and `progress`: every track with its `delta` since the previous scan;
        with `name`, that track's domains and trend too."""
        track = self.track(name) if name else None
        return {"tracks": add_deltas(self.summary(), history, scan), "problems": self.problems,
                "track": track["name"] if track else None,
                "domains": self.by_domain(track) if track else [],
                "trend": trend(history, track["name"]) if track else []}

    def cards_json(self, name, cards):
        """{card id: numbers} of track `name` over each card's (and its finer cards') links;
        cards that link nothing the track counts are left out."""
        track = self.track(name)
        found = {card_id: self.of_links(track, cards.tree_focus(card_id)) for card_id in cards.cards} if track else {}
        return {card_id: numbers for card_id, numbers in found.items() if numbers["total"]}


# --- the view engine's part: a ring per box, a bar per overview domain ---

def add_view_progress(ctx, result, node_rows, overview, name):
    """Put track `name`'s numbers on the view: each shown box's (its own units plus the hidden
    classes folded into it) as `progress`, each overview domain's, and the whole app's
    (returned). Units hidden by the view's filters are not counted on boxes."""
    progress = Progress(ctx.graph, ctx.notes)
    track = progress.track(name)
    if track is None:
        return None
    per_box = {}
    for unit in track_units(progress.graph, ctx.notes, track):
        if unit.node.id in ctx.removed:
            continue
        for box, _ in result.folder.stand_ins(unit.node.id, "src"):
            per_box.setdefault(box, []).append(unit)
    for row in node_rows:
        if row["id"] in per_box:
            row["progress"] = count(progress.judge, track, per_box[row["id"]])
    if overview:
        domains = {r["domain"]: r for r in progress.by_domain(track)}
        for row in overview["domains"]:
            found = domains.get(row["name"])
            row["progress"] = score(found["done"], found["total"]) if found else None
    return {"track": track["name"], **progress.numbers(track)}


# --- history: one line per scan beside graph.json ---

def history_path(folder):
    return folder / HISTORY_FILE


def read_history(folder):
    """[{scan, scanned_at, tracks: {name: [done, total]}}] oldest first; unreadable lines skipped."""
    path = history_path(folder)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("tracks"), dict):
            rows.append(row)
    return rows


def history_row(scan, scanned_at, summary):
    return {"scan": scan, "scanned_at": scanned_at,
            "tracks": {t["name"]: [t["done"], t["total"]] for t in summary}}


def record_history(folder, scan, scanned_at, summary):
    """Save this scan's numbers (replacing an earlier row of the same scan), keep the last
    HISTORY_KEEP scans."""
    rows = [r for r in read_history(folder) if r.get("scan") != scan]
    rows = [*rows, history_row(scan, scanned_at, summary)][-HISTORY_KEEP:]
    folder.mkdir(parents=True, exist_ok=True)
    history_path(folder).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def previous_row(history, scan):
    """The newest history row of another scan than `scan` (the "since last scan" base)."""
    earlier = [r for r in history if r.get("scan") != scan]
    return earlier[-1] if earlier else None


def add_deltas(summary, history, scan):
    """Each summary row gets `delta` = done now minus done at the previous scan (None if the
    track was not counted then)."""
    before = previous_row(history, scan)
    for row in summary:
        old = before["tracks"].get(row["name"]) if before else None
        row["delta"] = row["done"] - old[0] if old else None
    return summary


def history_numbers(history, scan, numbers):
    """{delta, trend} of one track's current `numbers` ({track, done, ...}): the viewer's line."""
    row = {"name": numbers["track"], "done": numbers["done"]}
    return {"delta": add_deltas([row], history, scan)[0]["delta"], "trend": trend(history, numbers["track"])}


def trend(history, name):
    """[{scanned_at, done, total, percent}] of one track over the recorded scans."""
    return [{"scanned_at": r["scanned_at"], **score(*r["tracks"][name])}
            for r in history if name in r["tracks"]]
