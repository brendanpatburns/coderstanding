"""Location refs (DESIGN 7e): one text line that names a place in an app, to paste into a chat or
another program and resolve again later. `@<app> <target> [(<hint>)]`; the targets are listed in
`parse_target`. The only place that parses, writes, resolves or prints a ref; `ref` (CLI), `read`
and GET /api/ref all call it.

A `path:A-B` ref carries the enclosing unit as its hint, `(Class#function +3..+17)`: the offsets
are lines below the unit's first line, so the lines can be found again when the unit moved."""

import re

from .apps import AppError
from .cards import open_cards
from .fingerprints import SourceLines
from .readtargets import DOC_SECTION, PLACE, read_target_blocks
from .textout import format_card, format_comments, format_read, format_tour
from .tours import open_tours
from .views import miss, read_blocks, resolve_unit, unique_namer, unit_at
from .wordmatch import close_names

REF_START = re.compile(r"(?<![\w.])(`?)@([\w.\-]+)[ \t]+(\S.*)")
HINT = re.compile(r"(.*?)\s*\(([^()]*)\)")
HINT_UNIT = re.compile(r"([^\s()+]+)(?:\s+\+(\d+)\.\.\+(\d+))?")
TOUR = re.compile(r"tour\s+(\S+)(?:\s+step\s+(\d+)(?:\s+lines\s+(\d+)(?:-(\d+))?)?)?")
CARD = re.compile(r"(comment|spec|memo)\s+(\S+)")
MISS_SEP = "; did you mean: "


class RefMiss(AppError):
    """A ref that names nothing today; `did_you_mean` = ready-to-paste nearest targets."""

    def __init__(self, message, did_you_mean=()):
        super().__init__(message)
        self.did_you_mean = list(did_you_mean)


# --- parse and write -------------------------------------------------------------------------

def parse_ref(text, default_app=None):
    """The parsed ref of one line (`{app, kind, ...}`), or None when it is not a ref. Without
    `@app` the line is a bare target of `default_app`."""
    line = text.strip().strip("`").strip()
    match = re.fullmatch(r"@([\w.\-]+)[ \t]+(\S.*)", line)
    if match:
        return parse_rest(match[1], match[2])
    return parse_rest(default_app, line) if default_app and line else None


def parse_rest(app, rest):
    """The target and optional `(hint)` after `@app`."""
    target, hint = rest.strip(), None
    split = HINT.fullmatch(target)
    if split:
        target, hint = split[1].strip(), split[2].strip()
    parsed = parse_target(target)
    if parsed is None:
        return None
    parsed = {"app": app, **parsed}
    if hint:
        parsed["hint"] = hint
        if parsed["kind"] == "lines":
            parsed.update(hint_parts(hint))
    return parsed


def parse_target(target):
    """{kind, ...} of a target: `path:A-B` / `path:A` (lines), `doc.md#Heading`, `tour <id> [step N
    [lines A-B]]`, `comment|spec|memo <id>`, else a class / `Class#function` / function name."""
    tour, card = TOUR.fullmatch(target), CARD.fullmatch(target)
    section, place = DOC_SECTION.fullmatch(target), PLACE.fullmatch(target)
    if tour:
        parsed = {"kind": "tour", "tour": tour[1]}
        if tour[2]:
            parsed["step"] = int(tour[2])
        if tour[3]:
            parsed["lines"] = [int(tour[3]), int(tour[4] or tour[3])]
        return parsed
    if card:
        return {"kind": card[1], "id": card[2]}
    if section:
        return {"kind": "heading", "path": section[1], "heading": section[2].strip()}
    if place:
        return {"kind": "lines", "path": place[1], "lines": [int(place[2]), int(place[3] or place[2])]}
    return {"kind": "target", "target": target} if re.fullmatch(r"\S+", target) else None


def hint_parts(hint):
    """`hint_unit` and `hint_offset` of a hint like `Class#function +3..+17` ({} for free text)."""
    match = HINT_UNIT.fullmatch(hint)
    if not match:
        return {}
    parts = {"hint_unit": match[1]}
    if match[2]:
        parts["hint_offset"] = [int(match[2]), int(match[3])]
    return parts


def find_refs(text):
    """Every ref in pasted text, in order: one per line, junk around it ignored, a ref in
    backticks ends at the closing one."""
    found = []
    for line in text.splitlines():
        match = REF_START.search(line)
        if not match:
            continue
        rest = match[3].split("`")[0] if match[1] else match[3].rstrip("` \t")
        parsed = parse_rest(match[2], rest)
        if parsed:
            found.append(parsed)
    return found


def format_ref(parsed):
    """The text form of a parsed ref (the inverse of `parse_ref`)."""
    kind = parsed["kind"]
    if kind == "lines":
        first, last = parsed["lines"]
        target = f"{parsed['path']}:{first}" + (f"-{last}" if last != first else "")
    elif kind == "heading":
        target = f"{parsed['path']}#{parsed['heading']}"
    elif kind == "tour":
        target = f"tour {parsed['tour']}"
        if "step" in parsed:
            target += f" step {parsed['step']}"
        if "lines" in parsed:
            first, last = parsed["lines"]
            target += f" lines {first}" + (f"-{last}" if last != first else "")
    elif kind == "target":
        target = parsed["target"]
    else:
        target = f"{kind} {parsed['id']}"
    return f"@{parsed['app']} {target}" + (f" ({parsed['hint']})" if parsed.get("hint") else "")


# --- resolve ---------------------------------------------------------------------------------

def resolve_ref(app, graph, notes, parsed):
    """What a parsed ref points at today, as the GET /api/ref JSON (without `ok`): `kind`, `app`,
    `path`/`lines`/`target`/`tour`/`step`/`comment`/`card` as they apply, `view` (the viewer URL
    options that show it) and `warning` when the place is not certain. RefMiss when it is gone."""
    kind = parsed["kind"]
    if kind in ("lines", "heading", "target"):
        return resolve_code(app, graph, notes, parsed)
    if kind == "tour":
        return resolve_tour(app, graph, notes, parsed)
    if kind == "comment":
        return resolve_comment(app, graph, notes, parsed["id"])
    return resolve_card(app, graph, notes, kind, parsed["id"])


def with_misses(call):
    """Run `call`; an AppError saying `...; did you mean: a, b` becomes a RefMiss with the list."""
    try:
        return call()
    except RefMiss:
        raise
    except AppError as error:
        message, _, near = str(error).partition(MISS_SEP)
        raise RefMiss(message, near.split(", ") if near else ()) from error


def read_range(blocks):
    """(file, first line, last line) of the blocks `read` printed."""
    return blocks[0]["file"], min(b["line"] for b in blocks), max(b["end"] for b in blocks)


def unit_name(unit):
    return unit[0] + (f"#{unit[1]}" if unit[1] else "")


def lines_view(unit, path, lines):
    """Viewer options for lines of a file: the class or function around them, or just the file."""
    view = {"select": unit_name(unit)} if unit else {}
    return {**view, "file": path, "hl": f"{lines[0]}-{lines[1]}"}


def code_target(parsed, graph, notes):
    """(read target text, unit or None) of a `heading` or `target` ref."""
    if parsed["kind"] == "heading":
        return f"{parsed['path']}#{parsed['heading']}", None
    if parsed["kind"] == "target":
        unit = with_misses(lambda: resolve_unit(graph, notes, parsed["target"]))
        return unit_name(unit), unit
    return None, None


def resolve_code(app, graph, notes, parsed):
    kind, source = parsed["kind"], SourceLines(app.root).of
    text, unit = code_target(parsed, graph, notes)
    warning = None
    if kind == "lines":
        path, lines, warning = relocate(app, graph, notes, parsed)
        text = f"{path}:{lines[0]}-{lines[1]}"
    blocks = with_misses(lambda: read_target_blocks(app, graph, notes, text, False, None, source,
                                                    unique_namer(graph)))
    path, first, last = read_range(blocks)
    if first > len(source(path)):
        raise RefMiss(f"{path} has only {len(source(path))} lines, not {first}")
    data = {"kind": kind, "app": app.name, "path": path, "lines": [first, last]}
    if kind == "target":
        data.update(kind="function" if unit[1] else "node", target=text, view={"select": text})
    else:
        unit = unit_at(graph, path, first) if kind == "lines" else None
        data.update(view=lines_view(unit, path, data["lines"]))
        if unit:
            data["target"] = unit_name(unit)
    return {**data, **({"warning": warning} if warning else {})}


def relocate(app, graph, notes, parsed):
    """(path, [first, last], warning) of a `lines` ref: the lines as written, unless the hint's
    unit moved since the copy (then its offsets, with a warning). Without offsets the lines stay
    and only a warning says they left the hinted unit."""
    path, (first, last) = parsed["path"], parsed["lines"]
    name, offset = parsed.get("hint_unit"), parsed.get("hint_offset")
    try:
        unit = resolve_unit(graph, notes, name) if name else None
    except AppError:
        unit = None
    if unit is None:
        warning = f"hint {name!r} is not found today; showing the lines as written" if offset else None
        return path, [first, last], warning
    here = unit_at(graph, path, first) if (app.root / path).is_file() else None
    if here == unit and not offset:
        return path, [first, last], None
    blocks = read_blocks(graph, notes, unit)
    start = min(blocks, key=lambda b: abs(b["line"] - first))
    if not offset:
        return path, [first, last], f"lines {first}-{last} are no longer in {name}; showing them as written"
    lines = [start["line"] + offset[0], start["line"] + offset[1]]
    if lines == [first, last] and start["file"] == path:
        return path, lines, None
    return start["file"], lines, (f"{name} moved: now at {start['file']}:{start['line']}; showing "
                                  f"lines {lines[0]}-{lines[1]} (copied as {first}-{last}), check them")


def step_of(tours, parsed):
    """(tour, step number, tour json) of a tour ref; RefMiss for an unknown tour or step."""
    try:
        tour = tours.get(parsed["tour"])
    except KeyError:
        near = close_names([parsed["tour"]], tours.ids())
        raise RefMiss(f"no tour {parsed['tour']}", near) from None
    number = parsed.get("step")
    if number is not None and not 1 <= number <= len(tour.steps):
        raise RefMiss(f"{tour.id} has steps 1-{len(tour.steps)}, not {number}")
    return tour, number, tours.tour_json(tour)


def tour_view(tour_id, number=None, **more):
    view = {"tab": "tours", "tour": tour_id}
    return {**view, **({"step": number} if number else {}), **more}


def resolve_tour(app, graph, notes, parsed):
    tours = open_tours(app, graph, notes)
    tour, number, data = step_of(tours, parsed)
    answer = {"kind": "tour", "app": app.name, "tour": tour.id}
    if number is None:
        return {**answer, "view": tour_view(tour.id)}
    answer.update(kind="step", step=number)
    code = data["steps"][number - 1]["code"]
    if code and code["path"]:
        answer.update(path=code["path"], lines=[code["from"], code["to"]])
    if "lines" in parsed:
        first, last = parsed["lines"]
        if not code or code["from"] is None or last < code["from"] or first > code["to"]:
            shown = f"{code['from']}-{code['to']}" if code and code["from"] else "no lines"
            raise RefMiss(f"{tour.id} step {number} shows {shown}, not {first}-{last}")
        answer["lines"] = [max(first, code["from"]), min(last, code["to"])]
    return {**answer, "view": tour_view(tour.id, number, **(
        {"hl": f"{answer['lines'][0]}-{answer['lines'][1]}"} if "lines" in parsed else {}))}


def resolve_comment(app, graph, notes, comment_id):
    tours = open_tours(app, graph, notes)
    try:
        _, _, comment = tours.comments.find(comment_id)
    except KeyError:
        raise RefMiss(f"no comment {comment_id}") from None
    _, tour_id, key = tours.comments.split(comment["target"])
    tour, step = tours.step_of(tour_id + (f"#{key}" if key else ""))
    number = tour.steps.index(step) + 1 if tour and step else None
    answer = {"kind": "comment", "app": app.name, "comment": comment_id, "tour": tour_id}
    if number:
        answer["step"] = number
    return {**answer, "view": tour_view(tour_id, number, comment=comment_id)}


def resolve_card(app, graph, notes, kind, card_id):
    cards = open_cards(app, graph, notes, kind)
    if card_id not in cards.cards:
        raise RefMiss(f"no {kind} {card_id}", close_names([card_id], list(cards.cards)))
    view = {"specs": "1", "spec": card_id} if kind == "spec" else {"notes": "1", "note": card_id}
    return {"kind": kind, "app": app.name, "card": card_id, "view": view}


def resolve_json(app, graph, notes, parsed):
    """`resolve_ref` as the API answers: `ok` first; a miss is `{ok: false, error, did_you_mean}`."""
    try:
        return {"ok": True, **with_misses(lambda: resolve_ref(app, graph, notes, parsed))}
    except RefMiss as error:
        return {"ok": False, "error": str(error), "did_you_mean": error.did_you_mean}


# --- print -----------------------------------------------------------------------------------

def ref_text(app, graph, notes, parsed):
    """What `ref` prints for one ref: the same text as `read`, `tour --step`, the comment thread
    or `spec` / `memo`, then a `warning:` line when the place is uncertain."""
    found = with_misses(lambda: resolve_ref(app, graph, notes, parsed))
    kind = found["kind"]
    if kind in ("lines", "heading", "node", "function"):
        text = code_text(app, graph, notes, parsed, found)
    elif kind in ("tour", "step"):
        text = tour_text(app, graph, notes, parsed, found)
    elif kind == "comment":
        text = comment_text(app, graph, notes, found)
    else:
        cards = open_cards(app, graph, notes, kind)
        text = format_card(app.name, cards.card_json(cards.get(found["card"])))
    return text + (f"\nwarning: {found['warning']}" if found.get("warning") else "")


def code_text(app, graph, notes, parsed, found):
    first, last = found["lines"]
    target = found.get("target") if found["kind"] in ("node", "function") else (
        f"{parsed['path']}#{parsed['heading']}" if found["kind"] == "heading"
        else f"{found['path']}:{first}-{last}")
    source, namer = SourceLines(app.root).of, unique_namer(graph)
    blocks = read_target_blocks(app, graph, notes, target, False, None, source, namer)
    return format_read(app.name, [target], blocks, source, namer)


def tour_text(app, graph, notes, parsed, found):
    tours = open_tours(app, graph, notes)
    data = tours.tour_json(tours.get(found["tour"]))
    if "lines" not in parsed:
        return format_tour(app.name, data, found.get("step"))
    first, last = found["lines"]
    step = data["steps"][found["step"] - 1]
    marked = {**step["code"], "mark": list(range(first, last + 1))}
    notes_here = [n for n in step.get("notes") or []
                  if n["from"] is not None and n["from"] <= last and n["to"] >= first]
    data["steps"][found["step"] - 1] = {**step, "code": marked, "notes": notes_here}
    return format_tour(app.name, data, found["step"])


def comment_text(app, graph, notes, found):
    tours = open_tours(app, graph, notes)
    data = tours.tour_json(tours.get(found["tour"]))
    every = tours.comments.list_json(f"tour:{found['tour']}")
    thread = next(c["thread"] for c in every if c["id"] == found["comment"])
    comments = [c for c in every if c["thread"] == thread]
    return format_comments(app.name, data, comments, show_resolved=True)
