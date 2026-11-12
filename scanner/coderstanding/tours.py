"""Tours (docs/DESIGN.md 7c): guided walks through the code, one Markdown file per tour in
`<data>/tours/<id>.md` (items.py does the file format), recordings in `<data>/tours/<id>/`.

A step has a stable `key`, `targets` (CLI syntax), `## Title` + text in the body, and an
optional code excerpt anchored on a class or function (`at`), never on raw line numbers:
`lines: +a..+b` are offsets from the anchor's first line, clipped to its span (cut as
fingerprints.py cuts them), `mark` the offsets to highlight. `fp` = fingerprints.digest of the
excerpt when written: a different digest today = the step is stale ("code changed since
written") until it is re-saved, accepted or confirmed (`still true`), like notes (10).
Authors follow the notes model: `by` / `ai` per tour and per step; a human edit or accept
turns an AI draft into the human's step.
Line notes (`notes: [{lines: +a..+b, text, cue}]` on a step with code) are short callouts on
lines of the excerpt, offsets like `mark`; the viewer pops them up as the narration reaches
`cue` (words of the step text), else in order through the step. They belong to the step's author.

"""

import re
import secrets
from dataclasses import dataclass, field

from .comments import Comments
from .fingerprints import SourceLines, class_end, digest, function_spans
from .items import (Item, ItemError, ItemStore, check_id, focus_ids, join_sections, parse_scalar,
                    stored_target, split_sections, unknown_targets)
from .notes import AI_AUTHOR, ARROW, FUNCTION_SEP, HUMAN_AUTHOR, edge_key, function_key, node_key
from .views import ViewContext, effective_level, start_paths, unique_namer

KIND = "tours"
COMMENT_KIND = "tour"  # comment targets `tour:<id>` and `tour:<id>#<step key>`
AUDIO_TYPES = {"audio/webm": "webm", "audio/ogg": "ogg"}  # upload type -> file extension
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # about 20 minutes of 32 kbps Opus
DRAFT_LINES = 8  # excerpt length of an AI draft's step
OFFSETS = re.compile(r"^\+?(\d+)(?:\.\.\+?(\d+))?$")
VIEW_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")  # a view option's URL name (viewer/view_options.js)


# --- the model ---

@dataclass
class Code:
    at: str  # class or function target the lines hang on
    first: int = 0  # offsets from the anchor's first line
    last: int = 0
    mark: list = field(default_factory=list)  # offsets of highlighted lines
    fp: str = None  # digest of the excerpt when written
    was: str = None  # "from-to" line numbers when fp was taken


@dataclass
class LineNote:
    first: int  # offsets from the code anchor's first line, like Code.mark
    last: int
    text: str
    cue: str = None  # words of the step text the note pops up at (None: in order through the step)


@dataclass
class Step:
    key: str
    title: str = ""
    text: str = ""
    targets: list = field(default_factory=list)
    code: Code = None
    by: str = HUMAN_AUTHOR
    ai: bool = False
    rec: dict = None  # recording: {by, fp (digest of the spoken text), dur, ext}
    level: int = None
    notes: list = field(default_factory=list)  # LineNote callouts on the code lines
    view: dict = field(default_factory=dict)  # view options for this step only (over the tour's)


@dataclass
class Tour:
    id: str
    title: str
    by: str = HUMAN_AUTHOR
    ai: bool = False
    steps: list = field(default_factory=list)
    start: bool = False  # a "Start here" path: a short tour from an entry class (views.start_paths)
    view: dict = field(default_factory=dict)  # view options while it plays: URL names -> values


def clean_view(data, strict=False):
    """A `view:` map: option URL names (`lines`, `selfcalls`, `hide_kind`) -> a scalar or a list of
    scalars. The viewer's table (viewer/view_options.js) knows the names and values and skips the
    ones it does not know, so a newer or older tour still loads; `strict` (writes) raises on a bad
    shape, else bad entries are dropped."""
    if data in (None, ""):
        return {}
    if not isinstance(data, dict):
        if strict:
            raise ValueError(f"view must be a map of option names to values, got {data!r}")
        return {}
    out = {}
    for name, value in data.items():
        scalar = (str, int, float, bool)
        ok = VIEW_NAME.match(str(name)) and (isinstance(value, scalar) or (
            isinstance(value, list) and all(isinstance(v, scalar) for v in value)))
        if ok:
            out[str(name)] = value
        elif strict:
            raise ValueError(f"bad view option {name!r}: {value!r}")
    return out


def parse_view_pairs(pairs):
    """CLI `--view key=value ...` -> {key: value}; `a,b` is a list, `key=` (empty) removes it (None)."""
    out = {}
    for pair in pairs or []:
        name, sep, text = pair.partition("=")
        if not sep or not VIEW_NAME.match(name.strip()):
            raise ValueError(f"--view takes key=value, got {pair!r}")
        text = text.strip()
        if not text:
            out[name.strip()] = None
        elif "," in text:
            out[name.strip()] = [parse_scalar(t) for t in text.split(",") if t.strip()]
        else:
            out[name.strip()] = parse_scalar(text)
    return out


def parse_offsets(text):
    """`+6..+7` -> (6, 7); `+6` -> (6, 6)."""
    match = OFFSETS.match(str(text).strip())
    if not match:
        raise ItemError(f"lines must look like +a..+b, got {text!r}")
    first = int(match.group(1))
    last = int(match.group(2)) if match.group(2) is not None else first
    if last < first:
        raise ItemError(f"lines end before they start: {text!r}")
    return first, last


def offsets_text(first, last):
    return f"+{first}..+{last}"


def parse_mark(values):
    return sorted({parse_offsets(v)[0] for v in values or []})


def new_key(taken):
    while True:
        key = secrets.token_hex(3)
        if key not in taken:
            return key


def code_from_meta(data):
    if not data:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("at"), str):
        raise ItemError(f"code needs an `at` target: {data!r}")
    first, last = parse_offsets(data.get("lines", "+0..+0"))
    return Code(data["at"], first, last, parse_mark(data.get("mark")),
                data.get("fp") and str(data["fp"]), data.get("was") and str(data["was"]))


def code_to_meta(code):
    data = {"at": code.at, "lines": offsets_text(code.first, code.last)}
    if code.mark:
        data["mark"] = [f"+{m}" for m in code.mark]
    for name in ("fp", "was"):
        if getattr(code, name):
            data[name] = getattr(code, name)
    return data


def notes_from_meta(rows):
    notes = []
    for row in rows or []:
        if not isinstance(row, dict) or not str(row.get("text") or "").strip():
            raise ItemError(f"a line note needs lines and text: {row!r}")
        first, last = parse_offsets(row.get("lines", "+0"))
        cue = str(row["cue"]).strip() if row.get("cue") else None
        notes.append(LineNote(first, last, str(row["text"]).strip(), cue or None))
    return notes


def notes_to_meta(notes):
    return [{"lines": offsets_text(n.first, n.last), "text": n.text, **({"cue": n.cue} if n.cue else {})}
            for n in notes]


def tour_from_item(item):
    meta = item.meta
    by, ai = str(meta.get("by") or HUMAN_AUTHOR), bool(meta.get("ai", False))
    rows = meta.get("steps") or []
    sections = split_sections(item.body)
    if len(sections) != len(rows):
        raise ItemError(f"tour {item.id}: {len(rows)} steps but {len(sections)} ## sections")
    steps, taken = [], set()
    for row, (title, text) in zip(rows, sections):
        if not isinstance(row, dict):
            raise ItemError(f"tour {item.id}: a step must be a map, got {row!r}")
        key = str(row.get("key") or new_key(taken))
        taken.add(key)
        rec = (row.get("audio") or {}).get("rec")
        steps.append(Step(key, title, text, [str(t) for t in row.get("targets") or []],
                          code_from_meta(row.get("code")), str(row.get("by", by)),
                          bool(row.get("ai", ai if "by" not in row else False)),
                          rec if isinstance(rec, dict) else None, row.get("level"),
                          notes_from_meta(row.get("notes")), clean_view(row.get("view"))))
    return Tour(item.id, str(meta.get("title") or item.id), by, ai, steps,
                bool(meta.get("start", False)), clean_view(meta.get("view")))


def tour_to_item(tour):
    rows = []
    for step in tour.steps:
        row = {"key": step.key, "targets": step.targets}
        if step.code:
            row["code"] = code_to_meta(step.code)
        if step.notes:
            row["notes"] = notes_to_meta(step.notes)
        if step.level is not None:
            row["level"] = step.level
        if step.view:
            row["view"] = step.view
        if step.by != tour.by:
            row["by"] = step.by
        if step.ai != (tour.ai if step.by == tour.by else False):
            row["ai"] = step.ai
        if step.rec:
            row["audio"] = {"rec": step.rec}
        rows.append(row)
    meta = {"title": tour.title, "by": tour.by, **({"ai": True} if tour.ai else {}),
            **({"start": True} if tour.start else {}), **({"view": tour.view} if tour.view else {}),
            "steps": rows}
    return Item(tour.id, meta, join_sections([(s.title, s.text) for s in tour.steps]))


def spoken_fp(step):
    """What a recording was made for: the step's title and text (DESIGN 7c Audio)."""
    return digest([step.title, step.text])


# --- code excerpts: anchors resolved against today's graph ---

@dataclass
class Excerpt:
    path: str
    start: int  # the anchor's first line today
    from_: int
    to: int
    mark: list
    lines: list

    @property
    def range(self):
        return f"{self.from_}-{self.to}"


def anchor_span(graph, notes, at):
    """(file, first line, end line exclusive or None = file end) of a class or function target;
    None when it is gone (or names a connection or outside code)."""
    found = stored_target(graph, notes, at)
    if not found or found[0] == "edge":
        return None
    node = graph.nodes[found[1]]
    if node.kind == "external" or not node.file:
        return None
    if found[0] == "function":
        spans = function_spans(node).get(found[2])
        return (node.file, *spans[0]) if spans else None
    return node.file, node.line, class_end(node)


def excerpt(graph, notes, source, code):
    """Today's lines of a step's code, or None when its anchor is gone."""
    span = anchor_span(graph, notes, code.at)
    if span is None:
        return None
    path, first, end = span
    last_line = max((end - 1) if end else len(source.of(path)), first)
    low = min(first + code.first, last_line)
    high = min(max(first + code.last, low), last_line)
    return Excerpt(path, first, low, high, [first + m for m in code.mark if low <= first + m <= high],
                   source.span(path, low, high + 1))


def parse_line_range(text):
    """`50-51` -> (50, 51); `50` -> (50, 50): today's line numbers (CLI notes)."""
    match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", str(text))
    if not match:
        raise ValueError(f"lines must look like 50 or 50-52, got {text!r}")
    low, high = int(match.group(1)), int(match.group(2) or match.group(1))
    if high < low:
        raise ValueError(f"lines end before they start: {text!r}")
    return low, high


def note_at(start, code, low, high, text, cue=None):
    """A line note on today's lines low..high of an excerpt whose anchor starts at `start`,
    clipped to the step's lines."""
    first = min(max(low - start, code.first), code.last)
    last = min(max(high - start, first), code.last)
    cue = str(cue).strip() if cue else None
    return LineNote(first, last, text.strip(), cue or None)


def notes_json(step, found):
    """The step's line notes in today's line numbers (None when the anchor is gone), in line order."""
    start = found.start if found else None
    return [{"from": None if start is None else start + n.first, "to": None if start is None else start + n.last,
             "text": n.text, "cue": n.cue} for n in step.notes]


# --- the store of one app ---

class Tours:
    """The tours of one app against its graph: read, write, resolve, stamp, audio."""

    def __init__(self, data_dir, graph, notes, source):
        self.store = ItemStore(data_dir, KIND)
        self.graph, self.notes, self.source = graph, notes, source
        self.comments = Comments(data_dir, locate=self.locate)  # DESIGN 7d: `tour:<id>[#<key>]`
        self.comments.add_kind(COMMENT_KIND, KIND, self)

    # comments: the hooks comments.py asks a kind for
    def locate(self, at):
        """(path, first line today) of a class or function anchor, or None."""
        span = anchor_span(self.graph, self.notes, at)
        return span[:2] if span else None

    def step_of(self, rest):
        """(tour, step or None) of `<id>` / `<id>#<key>`; (None, None) when gone."""
        tour_id, _, key = rest.partition("#")
        try:
            tour = self.get(tour_id)
        except (KeyError, ItemError):
            return None, None
        return tour, next((s for s in tour.steps if s.key == key), None) if key else None

    def comment_exists(self, rest):
        tour, step = self.step_of(rest)
        return tour is not None and ("#" not in rest or step is not None)

    def comment_target(self, text):
        """A target as the CLI takes it: `tour:<id>#<N>` (a step number) becomes the step's key."""
        kind, _, rest = text.partition(":")
        tour_id, _, part = rest.partition("#")
        if kind == COMMENT_KIND and part.isdigit():
            tour, step = self.step_of(rest)
            if tour and step is None and 1 <= int(part) <= len(tour.steps):
                return f"{kind}:{tour_id}#{tour.steps[int(part) - 1].key}"
        return text

    def comment_anchor(self, rest):
        _, step = self.step_of(rest)
        return step.code.at if step and step.code else None

    # reading
    def ids(self):
        return self.store.ids()

    def get(self, tour_id):
        return tour_from_item(self.store.read(tour_id))

    def all(self):
        return [self.get(i) for i in self.ids()]

    def excerpt(self, step):
        return excerpt(self.graph, self.notes, self.source, step.code) if step.code else None

    def is_stale(self, step, found=None):
        found = found or self.excerpt(step)
        now = digest(found.lines) if found else None
        return bool(step.code and step.code.fp and now and now != step.code.fp)

    def lost_targets(self, step):
        return unknown_targets(self.graph, self.notes, step.targets)

    def is_lost(self, step, found=None):
        return bool(self.lost_targets(step)) or bool(step.code and not (found or self.excerpt(step)))

    def search(self, query=""):
        """Tours whose title, step titles, text or targets contain every word of the query."""
        hits = self.store.search(query.split(), lambda item: " ".join(
            [item.body] + [" ".join(map(str, row.get("targets") or []))
                           for row in item.meta.get("steps") or [] if isinstance(row, dict)]))
        return [tour_from_item(item) for item in hits]

    # views of a step (DESIGN 7c: the step's view is compute_view(level, focus, expand))
    def focus_of(self, step):
        """The step's targets as view ids (`A`, `A→B`, `A#f`); unknown ones left out."""
        return focus_ids(self.graph, self.notes, step.targets)

    def level_of(self, step, focus):
        """The step's level, else the lowest level that shows every target class."""
        if step.level is not None:
            return step.level
        classes = {i for f in focus for i in f.split(ARROW)}
        classes = {c.rpartition(FUNCTION_SEP)[0] if c not in self.graph.nodes else c for c in classes}
        levels = [effective_level(self.graph, self.notes, c) for c in classes if c in self.graph.nodes]
        return max(levels, default=1)

    # JSON (the viewer's contract; see DESIGN 7c)
    def step_json(self, tour, step, app_name=None):
        found = self.excerpt(step)
        stale = self.is_stale(step, found)
        focus = self.focus_of(step)
        code = None
        if step.code:
            code = {"at": step.code.at, "path": found.path if found else None,
                    "first": found.start if found else None,  # the anchor's first line: `lines` offsets
                    "from": found.from_ if found else None, "to": found.to if found else None,
                    "mark": found.mark if found else [], "lines": found.lines if found else [],
                    "was": step.code.was if stale else None}
        rec = None
        if step.rec:
            url = f"/api/tours/{tour.id}/audio/{step.key}" + (f"?app={app_name}" if app_name else "")
            rec = {"by": step.rec.get("by"), "dur": step.rec.get("dur"),
                   "stale": step.rec.get("fp") != spoken_fp(step), "url": url}
        return {"key": step.key, "title": step.title, "text": step.text, "targets": step.targets,
                "by": step.by, "ai": step.ai, "stale": stale, "notes": notes_json(step, found),
                "lost": self.is_lost(step, found), "lost_targets": self.lost_targets(step),
                "code": code, "audio": {"rec": rec},
                "level": self.level_of(step, focus), "focus": focus, "view": dict(step.view),
                "expand": sorted({f.rpartition(FUNCTION_SEP)[0] for f in focus
                                  if ARROW not in f and f not in self.graph.nodes})}

    def tour_json(self, tour, app_name=None):
        """The tour; `comments` = open threads on the tour (all of it) and per step (DESIGN 7d)."""
        target = f"{COMMENT_KIND}:{tour.id}"
        counts = self.comments.open_counts(target)
        steps = [{**self.step_json(tour, s, app_name), "comments": counts.get(f"{target}#{s.key}", 0)}
                 for s in tour.steps]
        return {"id": tour.id, "title": tour.title, "by": tour.by, "ai": tour.ai,
                "start": tour.start, "view": dict(tour.view), "comments": sum(counts.values()), "steps": steps}

    def row_json(self, tour):
        return {"id": tour.id, "title": tour.title, "by": tour.by, "ai": tour.ai,
                "start": tour.start, "steps": len(tour.steps), "changed": sum(self.is_stale(s) for s in tour.steps),
                "ai_steps": sum(s.ai for s in tour.steps),
                "comments": sum(self.comments.open_counts(f"{COMMENT_KIND}:{tour.id}").values())}

    def list_json(self, query=""):
        return {"tours": [self.row_json(t) for t in self.search(query)]}

    # writing
    def save(self, tour):
        self.store.write(tour_to_item(tour))
        self.drop_orphan_audio(tour)

    def stamp(self, step):
        """Remember the excerpt's digest and where it is now (re-saving, accepting, still true)."""
        found = self.excerpt(step)
        if step.code and found:
            step.code.fp, step.code.was = digest(found.lines), found.range

    def step_from_body(self, data, old, taken):
        """A step from the editor's JSON; `mark` comes as absolute line numbers of today."""
        if not isinstance(data, dict):
            raise ValueError("each step must be an object")
        title, text, targets = data.get("title"), data.get("text", ""), data.get("targets", [])
        if not isinstance(title, str) or not title.strip() or not isinstance(text, str):
            raise ValueError("a step needs a title and text")
        if not isinstance(targets, list) or not all(isinstance(t, str) and t for t in targets):
            raise ValueError("targets must be a list of target names")
        code = None
        if data.get("code"):
            raw = data["code"]
            if not isinstance(raw, dict) or not isinstance(raw.get("at"), str):
                raise ValueError("code needs an `at` target")
            first, last = parse_offsets(raw.get("lines", "+0..+0"))
            code = Code(raw["at"], first, last)
            span = anchor_span(self.graph, self.notes, code.at)
            marks = raw.get("mark") or []
            if not all(isinstance(m, int) for m in marks):
                raise ValueError("mark must be absolute line numbers")
            if span:
                code.mark = sorted({m - span[1] for m in marks if first <= m - span[1] <= last})
            elif old and old.code and old.code.at == code.at:
                code.mark = old.code.mark
        key = data.get("key") if isinstance(data.get("key"), str) and data.get("key") else None
        step = Step(key or new_key(taken), title.strip(), text.strip(), targets, code,
                    level=data.get("level") if isinstance(data.get("level"), int) else None,
                    view=clean_view(data["view"], strict=True) if "view" in data
                    else dict(old.view) if old else {})  # an older editor sends no view: kept
        step.notes = self.notes_from_body(data.get("notes"), code, old)
        return step

    def notes_from_body(self, rows, code, old):
        """Line notes from the editor: `[{from, to, text, cue?}]` in today's line numbers. A lost
        anchor keeps the step's old notes (their lines cannot be placed)."""
        if not code:
            if rows:
                raise ValueError("line notes need the step's code")
            return []
        span = anchor_span(self.graph, self.notes, code.at)
        if not span:
            return old.notes if old and old.code and old.code.at == code.at else []
        if not rows:
            return []
        if not isinstance(rows, list):
            raise ValueError("notes must be a list")
        notes = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("from"), int) \
                    or not isinstance(row.get("text"), str) or not row["text"].strip():
                raise ValueError("a line note needs {from, to, text}")
            to = row.get("to") if isinstance(row.get("to"), int) else row["from"]
            notes.append(note_at(span[1], code, row["from"], to, row["text"], row.get("cue")))
        return notes

    def put(self, tour_id, body, by=HUMAN_AUTHOR):
        """The whole tour from the editor. Steps keep their keys; a new or changed step is the
        editor's (by, not AI) and its fp is taken again; unchanged steps stay as they were."""
        check_id(tour_id)
        if not isinstance(body, dict) or not isinstance(body.get("title"), str) \
                or not body["title"].strip() or not isinstance(body.get("steps"), list):
            raise ValueError("send {title, steps: [...]}")
        old = self.get(tour_id) if self.store.exists(tour_id) else None
        old_steps = {s.key: s for s in old.steps} if old else {}
        steps, taken = [], set(old_steps)
        for data in body["steps"]:
            key = data.get("key") if isinstance(data, dict) else None
            before = old_steps.get(key) if key in old_steps else None
            step = self.step_from_body(data, before, taken)
            if step.key in {s.key for s in steps}:
                raise ValueError(f"step key {step.key} used twice")
            taken.add(step.key)
            if before and same_content(before, step):
                before.view = step.view  # view options are no spoken content: no new author or fp
                step = before
            else:
                step.by, step.ai = by, False
                step.rec = before.rec if before else None
                self.stamp(step)
            steps.append(step)
        title = body["title"].strip()
        view = clean_view(body["view"], strict=True) if "view" in body else dict(old.view) if old else {}
        tour = Tour(tour_id, title, old.by if old else by, old.ai if old else False, steps,
                    old.start if old else bool(body.get("start")), view)
        if old and title != old.title:
            tour.by, tour.ai = by, False
        self.save(tour)
        return tour

    def pick(self, tour, keys):
        """The steps named by key (None: every step)."""
        if keys is None:
            return list(tour.steps)
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise ValueError("keys must be a list of step keys")
        unknown = set(keys) - {s.key for s in tour.steps}
        if unknown:
            raise ValueError(f"no such steps: {', '.join(sorted(unknown))}")
        return [s for s in tour.steps if s.key in keys]

    def still_true(self, tour_id, keys=None):
        """Re-stamp the steps (default: the stale ones) for the code as it is now."""
        tour = self.get(tour_id)
        steps = self.pick(tour, keys) if keys is not None else [s for s in tour.steps
                                                                 if self.is_stale(s)]
        for step in steps:
            self.stamp(step)
        self.save(tour)
        return tour, steps

    def accept(self, tour_id, keys=None, by=HUMAN_AUTHOR):
        """Turn AI draft steps (default: all, and the tour itself) into `by`'s; re-stamps them."""
        tour = self.get(tour_id)
        for step in self.pick(tour, keys):
            if step.ai:
                step.by, step.ai = by, False
                self.stamp(step)
        if keys is None and tour.ai:
            tour.by, tour.ai = by, False
        self.save(tour)
        return tour

    def edit_step(self, tour_id, number, by=AI_AUTHOR, ai=True, **fields):
        """`tour --step N --text ...`: change a step's title/text as `by` (an AI edit makes it an
        AI draft again); re-stamps it."""
        tour, step = self.numbered(tour_id, number)
        for name, value in fields.items():
            if value is not None:
                setattr(step, name, value.strip())
        step.by, step.ai = by, ai
        self.stamp(step)
        self.save(tour)
        return tour, step

    def edit_note(self, tour_id, number, lines, text=None, cue=None, by=AI_AUTHOR, ai=True):
        """`tour --step N --note A-B TEXT [--cue WORDS]` / `--drop-note A-B`: add, change (same
        lines) or, with text None, remove a line note as `by`; lines are today's numbers."""
        tour, step = self.numbered(tour_id, number)
        found = self.excerpt(step)
        if not found:
            raise ValueError(f"step {number} has no code to put a note on")
        low, high = parse_line_range(lines)
        keep = [n for n in step.notes if (found.start + n.first, found.start + n.last) != (low, high)]
        if text is None and len(keep) == len(step.notes):
            raise ValueError(f"step {number} has no note on lines {lines}")
        if text is not None:
            if not found.from_ <= low <= high <= found.to:
                raise ValueError(f"lines {lines} are outside step {number}'s code ({found.range})")
            keep.append(note_at(found.start, step.code, low, high, text, cue))
        step.notes = sorted(keep, key=lambda n: (n.first, n.last))
        step.by, step.ai = by, ai
        self.save(tour)
        return tour, step

    def set_view(self, tour_id, values, number=None):
        """`tour --view k=v ...` (the tour) or `--step N --view k=v ...` (that step only): set
        options, None removes one. Not content: authors and fingerprints stay."""
        tour, step = self.numbered(tour_id, number) if number else (self.get(tour_id), None)
        target = step or tour
        view = dict(target.view)
        for name, value in values.items():
            if value is None:
                view.pop(name, None)
            else:
                view[name] = value
        target.view = clean_view(view, strict=True)
        self.save(tour)
        return tour, target.view

    def numbered(self, tour_id, number):
        tour = self.get(tour_id)
        if not 1 <= number <= len(tour.steps):
            raise ValueError(f"{tour_id} has steps 1-{len(tour.steps)}, not {number}")
        return tour, tour.steps[number - 1]

    def draft(self, tour_id, title, targets, by=AI_AUTHOR, start=False):
        """An AI draft along the targets, in the given order: one step per target, anchored on
        its function (a connection: on the calling function, at the call), text from notes and
        code docs. Saved as `by` with ai: true until a human accepts it; `start` flags it as a
        Start-here path."""
        check_id(tour_id)
        if self.store.exists(tour_id):
            raise ValueError(f"tour {tour_id} exists: edit it with --step or in its file")
        steps, taken = [], set()
        for target in targets:
            found = stored_target(self.graph, self.notes, target)
            if found is None:
                raise ValueError(f"unknown target: {target}")
            step = Step(new_key(taken), target, self.draft_text(target, found), [target],
                        self.draft_code(target, found), by, True)
            taken.add(step.key)
            self.stamp(step)
            steps.append(step)
        tour = Tour(tour_id, title, by, True, steps, start)
        self.save(tour)
        return tour

    def draft_starts(self, title=None):
        """AI-draft one start tour (`start: true`) per start path (views.start_paths), named
        `start-<entry>`: ([drafted tours], [ids that already exist and were left alone]).
        `title` names the first one; the others are "Start here: <entry>"."""
        namer = unique_namer(self.graph)
        drafted, kept = [], []
        for path in start_paths(ViewContext(self.graph, self.notes)):
            entry = namer(path[0]["id"])
            tour_id = "start-" + (re.sub(r"[^a-z0-9]+", "-", entry.lower()).strip("-") or "here")
            if self.store.exists(tour_id):
                kept.append(tour_id)
                continue
            name = title if title and not drafted else f"Start here: {entry}"
            drafted.append(self.draft(tour_id, name, [namer(step["id"]) for step in path],
                                      start=True))
        return drafted, kept

    def draft_text(self, target, found):
        kind, *ids = found
        key = {"edge": lambda: edge_key(*ids), "function": lambda: function_key(*ids),
               "node": lambda: node_key(ids[0])}[kind]()
        parts = [self.notes.value(key, name) for name in ("hint", "description")]
        if kind == "node" and not any(parts):
            parts.append(self.graph.nodes[ids[0]].doc)
        words = " ".join(p.strip() for p in parts if p)
        return f"[[{target}]]" + (f": {words}" if words else ".")

    def draft_code(self, target, found):
        kind, *ids = found
        if kind != "edge":
            span = anchor_span(self.graph, self.notes, target)
            if span is None:
                return None
            end = span[2] or span[1] + DRAFT_LINES
            return Code(target, 0, max(min(end - span[1], DRAFT_LINES) - 1, 0))
        from_id, to = ids
        name = target.split(ARROW)[0].split(">")[0]
        node = self.graph.nodes[from_id]
        edges = [e for e in self.graph.edges.values() if (e.from_id, e.to) == (from_id, to)]
        places = [loc for e in edges for c in e.calls for loc in c.at] + [loc for e in edges
                                                                         for loc in e.at]
        for loc in (p for p in places if p.file == node.file and p.line >= node.line):
            for fn, spans in function_spans(node).items():
                for first, end in spans:
                    if first <= loc.line < (end or loc.line + 1):
                        off = loc.line - first
                        return Code(f"{name}{FUNCTION_SEP}{fn}", max(off - 2, 0), off + 2, [off])
            off = loc.line - node.line
            return Code(name, max(off - 2, 0), off + 2, [off])
        return None

    # audio (DESIGN 7c Audio): <data>/tours/<id>/<key>.rec.<ext>
    def audio_dir(self, tour_id):
        return self.store.folder / check_id(tour_id)

    def audio_path(self, tour_id, step):
        if not step.rec:
            return None
        path = self.audio_dir(tour_id) / f"{step.key}.rec.{step.rec.get('ext', 'webm')}"
        return path if path.is_file() else None

    def step_by_key(self, tour, key):
        step = next((s for s in tour.steps if s.key == key), None)
        if step is None:
            raise KeyError(f"tour {tour.id} has no step {key}")
        return step

    def set_audio(self, tour_id, key, data, content_type, dur=None, by=HUMAN_AUTHOR):
        if content_type not in AUDIO_TYPES:
            raise ValueError(f"send one of {', '.join(AUDIO_TYPES)}")
        if not data or len(data) > MAX_AUDIO_BYTES:
            raise ValueError(f"a recording must be 1 byte to {MAX_AUDIO_BYTES} bytes")
        tour = self.get(tour_id)
        step = self.step_by_key(tour, key)
        self.remove_audio_file(tour_id, step)
        ext = AUDIO_TYPES[content_type]
        folder = self.audio_dir(tour_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{step.key}.rec.{ext}").write_bytes(data)
        step.rec = {"by": by, "fp": spoken_fp(step), "dur": dur, "ext": ext}
        self.save(tour)
        return tour

    def delete_audio(self, tour_id, key):
        tour = self.get(tour_id)
        step = self.step_by_key(tour, key)
        self.remove_audio_file(tour_id, step)
        step.rec = None
        self.save(tour)
        return tour

    def remove_audio_file(self, tour_id, step):
        path = self.audio_path(tour_id, step)
        if path:
            path.unlink()

    def audio_file(self, tour_id, key):
        """(path, content type) of a step's recording; KeyError when it has none."""
        step = self.step_by_key(self.get(tour_id), key)
        path = self.audio_path(tour_id, step)
        if path is None:
            raise KeyError(f"step {key} has no recording")
        ext = step.rec.get("ext", "webm")
        return path, next(t for t, e in AUDIO_TYPES.items() if e == ext)

    def drop_orphan_audio(self, tour):
        """Deleting a step deletes its recording."""
        folder = self.audio_dir(tour.id)
        keep = {p.name for s in tour.steps if (p := self.audio_path(tour.id, s))}
        for path in folder.glob("*.rec.*") if folder.is_dir() else []:
            if path.name not in keep:
                path.unlink()

    def orphan_audio(self):
        """Recording files with no tour or step (listed with the lost notes)."""
        known = {i: self.get(i) for i in self.ids()}
        orphans = []
        for folder in sorted(p for p in self.store.folder.glob("*/") if p.is_dir()):
            tour = known.get(folder.name)
            keep = {p.name for s in tour.steps if (p := self.audio_path(tour.id, s))} if tour else set()
            orphans += [p.relative_to(self.store.folder).as_posix() for p in sorted(folder.glob("*.rec.*"))
                        if p.name not in keep]
        return orphans

    # app-wide lists (warnings, lost notes)
    def stale_steps(self):
        """[(tour id, step number)] of steps whose code changed since written."""
        return [(t.id, n) for t in self.all() for n, s in enumerate(t.steps, 1) if self.is_stale(s)]

    def lost_steps(self):
        """[(tour id, step number)] of steps whose anchor or targets are gone."""
        return [(t.id, n) for t in self.all() for n, s in enumerate(t.steps, 1) if self.is_lost(s)]

    def steps_over(self, units):
        """[(tour id, step number)] of steps whose code window covers a unit (class id, function or
        None): the steps that may go stale when that code changes."""
        spans = [span for span in map(self.unit_span, units) if span]
        return [(tour.id, number) for tour in self.all() for number, step in enumerate(tour.steps, 1)
                if step.code and self.window_covers(step.code, spans)]

    def unit_span(self, unit):
        """(file, first line, end line exclusive or None = file end) of a unit; None when unknown."""
        node_id, function = unit
        node = self.graph.nodes[node_id]
        if function:
            found = function_spans(node).get(function)
            return (node.file, *found[0]) if found else None
        return (node.file, node.line, class_end(node)) if node.file else None

    def window_covers(self, code, spans):
        """True when the step's code lines (offsets from its anchor) overlap one of the spans."""
        anchor = anchor_span(self.graph, self.notes, code.at)
        if anchor is None:
            return False
        file, first, end = anchor
        low, high = first + code.first, first + code.last
        return any(file == span_file and low < (span_end or high + 1) and span_first <= high
                   for span_file, span_first, span_end in spans)


def same_content(old, new):
    """True when the editor sent a step back unchanged (title, text, targets, code, level)."""
    def code_of(step):
        c = step.code
        return (c.at, c.first, c.last, c.mark) if c else None
    return ((old.title, old.text, old.targets, code_of(old), old.level, old.notes)
            == (new.title, new.text, new.targets, code_of(new), new.level, new.notes))


def open_tours(app, graph, notes):
    """The app's tours, excerpts read from the repo."""
    return Tours(app.data, graph, notes, SourceLines(app.root))
