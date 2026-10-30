"""Markdown item store (docs/DESIGN.md 7, 7b, 7c): one Markdown file per item in
`<data>/<kind>/<id>.md`, structure in a front matter block, free text in the body.

Shared by every Markdown kind (tours and specs now; diagrams later). It knows nothing about a
kind's fields: it reads, writes, lists and searches files, parses the front matter, splits the
body into `## Title` sections, turns wiki links into names, and lists targets the graph no
longer has (like lost notes). Each kind adds its own fields and checks on top.

The front matter is a small YAML subset (no YAML package needed): `key: value` lines, block
lists of maps (`- key: value` with indented continuation lines), flow lists `[a, b]` and flow
maps `{a: 1, b: [x]}`; scalars are plain text, numbers, true/false/null or "quoted" (JSON).
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .apps import AppError, parse_target
from .notes import ARROW, FUNCTION_SEP, is_function

FENCE = "---"
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
LINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")  # wiki links (DESIGN 7b): [[Name]], [[tour:id]]
SECTION_PATTERN = re.compile(r"^## (.*)$", re.MULTILINE)
PLAIN_SPECIALS = set("[]{},:#&*!|>'\"%@`")


class ItemError(ValueError):
    pass


# --- front matter: parse ---

def parse_scalar(text):
    text = text.strip()
    if text.startswith('"'):
        return json.loads(text)
    if text in ("null", "~", ""):
        return None
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def parse_flow(text, at=0):
    """(value, next index) of a flow value at text[at:]: [list], {map} or a scalar."""
    while at < len(text) and text[at] == " ":
        at += 1
    if at < len(text) and text[at] in "[{":
        closing, items, mapping = ("]", [], None) if text[at] == "[" else ("}", None, {})
        at += 1
        while True:
            while at < len(text) and text[at] in " ,":
                at += 1
            if at >= len(text):
                raise ItemError(f"unclosed {closing!r} in: {text}")
            if text[at] == closing:
                return (items if mapping is None else mapping), at + 1
            if mapping is None:
                value, at = parse_flow(text, at)
                items.append(value)
            else:
                colon = text.index(":", at)
                key = text[at:colon].strip()
                mapping[key], at = parse_flow(text, colon + 1)
    if at < len(text) and text[at] == '"':
        end = at + 1
        while text[end] != '"':
            end += 2 if text[end] == "\\" else 1
        return json.loads(text[at:end + 1]), end + 1
    end = at
    while end < len(text) and text[end] not in ",]}":
        end += 1
    return parse_scalar(text[at:end]), end


def parse_value(text):
    """A block value: a flow list/map or a quoted string, else the whole line as a plain scalar
    (as YAML reads `title: Items, carry and looks`)."""
    if not text.strip().startswith(("[", "{", '"')):
        return parse_scalar(text)
    value, end = parse_flow(text)
    if text[end:].strip():
        raise ItemError(f"unexpected text after value: {text}")
    return value


def strip_comment(line):
    """A line without its ` # comment` (a `#` inside a word, like `cli#run`, is kept)."""
    if line.lstrip().startswith("#"):
        return ""
    in_quote = False
    for i, char in enumerate(line):
        if char == '"' and (i == 0 or line[i - 1] != "\\"):
            in_quote = not in_quote
        elif char == "#" and not in_quote and i and line[i - 1] == " ":
            return line[:i].rstrip()
    return line.rstrip()


def parse_block(lines, at, indent):
    """(value, next index) of the block starting at lines[at] with this indent: a map or list."""
    if lines[at][1].startswith("- "):
        items = []
        while at < len(lines) and lines[at][0] == indent and lines[at][1].startswith("- "):
            first = lines[at][1][2:]
            sub = [(indent + 2, first)]
            at += 1
            while at < len(lines) and lines[at][0] > indent:
                sub.append(lines[at])
                at += 1
            items.append(parse_block(sub, 0, indent + 2)[0] if ":" in first and not
                         first.lstrip().startswith(("[", "{", '"')) else parse_value(first))
        return items, at
    mapping = {}
    while at < len(lines) and lines[at][0] == indent:
        key, sep, rest = lines[at][1].partition(":")
        if not sep:
            raise ItemError(f"expected key: value, got: {lines[at][1]}")
        at += 1
        if rest.strip():
            mapping[key.strip()] = parse_value(rest)
        elif at < len(lines) and lines[at][0] > indent:
            mapping[key.strip()], at = parse_block(lines, at, lines[at][0])
        else:
            mapping[key.strip()] = None
    return mapping, at


def parse_front_matter(text):
    """(front matter dict, body) of a Markdown item file."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        raise ItemError("missing front matter (first line must be ---)")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == FENCE)
    except StopIteration:
        raise ItemError("front matter has no closing ---") from None
    rows = [(len(line) - len(line.lstrip(" ")), line.strip())
            for line in map(strip_comment, lines[1:end]) if line.strip()]
    meta = parse_block(rows, 0, 0)[0] if rows else {}
    return meta, "\n".join(lines[end + 1:]).strip("\n")


# --- front matter: write ---

def dump_scalar(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    plain = (text and text == text.strip() and not set(text) & PLAIN_SPECIALS - {"#", ">", ":"}
             and " #" not in text and ": " not in text and not text.endswith(":")
             and text[0] not in "-?#>|&*!%@`'\"" and parse_scalar(text) == text)
    return text if plain else json.dumps(text, ensure_ascii=False)


def dump_flow(value):
    if isinstance(value, list):
        return "[" + ", ".join(dump_flow(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {dump_flow(v)}" for k, v in value.items()) + "}"
    return dump_scalar(value)


def dump_front_matter(meta):
    """Front matter text (with the --- fences): scalars and flow values one per line, a list
    of maps as a block list with one `key: value` line per field."""
    lines = [FENCE]
    for key, value in meta.items():
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            lines.append(f"{key}:")
            for item in value:
                for i, (k, v) in enumerate(item.items()):
                    lines.append(f"  {'- ' if i == 0 else '  '}{k}: {dump_flow(v)}")
        else:
            lines.append(f"{key}: {dump_flow(value)}")
    lines.append(FENCE)
    return "\n".join(lines)


# --- body: sections and wiki links ---

def split_sections(body):
    """[(title, text)] of the body's `## Title` sections, in order (text before the first is
    dropped)."""
    marks = list(SECTION_PATTERN.finditer(body))
    return [(m.group(1).strip(), body[m.end():marks[i + 1].start() if i + 1 < len(marks)
                                       else len(body)].strip())
            for i, m in enumerate(marks)]


def join_sections(sections):
    return "\n\n".join(f"## {title}\n{text}".rstrip() for title, text in sections)


def link_name(link):
    """What a wiki link reads as: `tour:scan-pipeline` -> `scan-pipeline`, `cli#run` stays."""
    prefix, sep, rest = link.partition(":")
    return rest if sep and prefix in ("spec", "diagram", "tour") else link


def plain_links(text, wrap="{}"):
    """The text with each `[[link]]` replaced by its name, put into `wrap` (e.g. "`{}`")."""
    return LINK_PATTERN.sub(lambda m: wrap.format(link_name(m.group(1))), text)


# --- targets (CLI syntax: Name, A>B, Name#function) ---

def resolve_target(graph, notes, text):
    """parse_target's answer for a graph target, or None when it is unknown (lost)."""
    try:
        kind, *ids = parse_target(graph, notes, text)
    except AppError:
        return None
    if any(i not in graph.nodes for i in (ids if kind == "edge" else ids[:1])):
        return None
    if kind == "function" and not is_function(graph, "#".join(ids)):
        return None
    return (kind, *ids)


def unknown_targets(graph, notes, targets):
    """The targets the graph no longer has (listed like lost notes, never dropped)."""
    return [t for t in targets if resolve_target(graph, notes, t) is None]


def focus_ids(graph, notes, targets):
    """The targets as view ids (`A`, `A→B`, `A#f`) for compute_view's focus; unknown ones left
    out (a tour step's or a spec's highlight)."""
    ids = []
    for target in targets:
        found = resolve_target(graph, notes, target)
        if found:
            ids.append({"edge": ARROW, "function": FUNCTION_SEP}.get(found[0], "").join(found[1:]))
    return ids


def slug(title, fallback="item"):
    """An id made from a title: `Effects pipeline` -> `effects-pipeline`."""
    return re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")[:80].strip("-") or fallback


def free_id(base, taken):
    """`base`, or `base-2`, `base-3`... when that id is taken."""
    item_id, number = base, 2
    while item_id in taken:
        item_id, number = f"{base[:76]}-{number}", number + 1
    return item_id


# --- the store ---

@dataclass
class Item:
    id: str
    meta: dict = field(default_factory=dict)
    body: str = ""


def check_id(item_id):
    if not isinstance(item_id, str) or not ID_PATTERN.match(item_id):
        raise ItemError(f"bad id {item_id!r}: use lowercase letters, digits, - and _")
    return item_id


class ItemStore:
    """The `<data>/<kind>/` folder of one app: one `<id>.md` per item."""

    def __init__(self, data_dir, kind):
        self.folder = Path(data_dir) / kind
        self.kind = kind

    def path(self, item_id):
        return self.folder / f"{check_id(item_id)}.md"

    def ids(self):
        return sorted(p.stem for p in self.folder.glob("*.md") if ID_PATTERN.match(p.stem))

    def exists(self, item_id):
        return self.path(item_id).is_file()

    def read(self, item_id):
        path = self.path(item_id)
        if not path.is_file():
            raise KeyError(f"no {self.kind} {item_id}")
        try:
            meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        except ItemError as error:
            raise ItemError(f"{path}: {error}") from error
        if meta.get("id", item_id) != item_id:
            raise ItemError(f"{path}: id {meta['id']!r} does not match the file name")
        return Item(item_id, meta, body)

    def write(self, item):
        path = self.path(item.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = dump_front_matter({"id": item.id, **{k: v for k, v in item.meta.items()
                                                     if k != "id"}})
        path.write_text(text + "\n" + (f"\n{item.body.strip()}\n" if item.body.strip() else ""),
                        encoding="utf-8")

    def delete(self, item_id):
        self.path(item_id).unlink(missing_ok=True)

    def all(self):
        return [self.read(i) for i in self.ids()]

    def search(self, words, text_of=lambda item: item.body):
        """Items whose id, title or `text_of(item)` contain every word (case-insensitive)."""
        words = [w.lower() for w in words if w]
        hits = []
        for item in self.all():
            text = " ".join([item.id, str(item.meta.get("title", "")), text_of(item)]).lower()
            if all(w in text for w in words):
                hits.append(item)
        return hits
