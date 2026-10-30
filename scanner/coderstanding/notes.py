"""Notes store for `.coderstanding/notes.jsonl` (docs/DESIGN.md 10).

One JSON object per line, sorted by key, each with its author `by` (a real name: the human's
`human_author()`, e.g. their git user.name (old lines: `you`), `claude` for AI writes) and `ai: true` on AI drafts. A key can have one line per
author: `{"key":"node:X","level":3,"by":"claude","ai":true}` and
`{"key":"node:X","hint":"..","by":"you"}`. Reading merges them field by field and human values
win. An AI write never sets a field a human already set; a human write replaces the AI drafts
and other people's value of that field (the newest human edit is the value). Old lines without `ai` count as AI when `by` is `claude`.
"""

import getpass
import json
import os
import subprocess
from pathlib import Path

from .checks import DOMAIN_PREFIX, WARNING_KINDS, glob_regex
from .model import function_names
from .offline import GIT_OFFLINE, GIT_OFFLINE_ENV
from .procs import run_quiet

AI_AUTHOR = "claude"  # author of AI writes (`coderstanding set` without --by)
HUMAN_AUTHOR = "you"  # old viewer writes; new ones use human_author() (a real name)
USER_ENV = "CODERSTANDING_USER"  # overrides the human's name (tests, shared machines)
FIELDS = {"hint": str, "description": str, "level": int, "domain": str,
          "bindings": list, "singleton": bool, "status": str, "tags": list, "rules": str,
          "review": str,
          "loaded_by": list,  # ids of classes that load this one by name (a registry, a tool)
          "loaded_how": str,  # short text for how: "auto-loaded by the language registry"
          "ignore_warnings": list,
          "fp": dict}  # field -> fingerprint of the code when that field was written
TEXT_FIELDS = ("hint", "description", "status", "tags", "rules", "review", "loaded_by",
               "loaded_how")  # edited in the viewer
FINGERPRINTED = ("hint", "description", "rules", "review")  # fields that get a code fingerprint
WRITABLE_FIELDS = (*TEXT_FIELDS, "ignore_warnings")  # the viewer may write these
STATUSES = ("stable", "wip", "fragile", "deprecated")
NODE_PREFIX = "node:"
EDGE_PREFIX = "edge:"
ARROW = "→"
FUNCTION_SEP = "#"


def node_key(node_id):
    return NODE_PREFIX + node_id


def function_key(node_id, name):
    """Notes key of one function of a class (DESIGN 12c): `node:<class>#<function>`."""
    return f"{NODE_PREFIX}{node_id}{FUNCTION_SEP}{name}"


def edge_key(from_id, to):
    return f"{EDGE_PREFIX}{from_id}{ARROW}{to}"


def domain_id(name):
    """View id of a domain card (never a graph node id)."""
    return DOMAIN_PREFIX + name


def check_value(name, value):
    """Raise ValueError for an unknown field or a value of the wrong type."""
    if name not in FIELDS:
        raise ValueError(f"unknown note field: {name} (known: {', '.join(FIELDS)})")
    wanted = FIELDS[name]
    if value is None:
        return
    if type(value) is not wanted:
        raise ValueError(f"{name} must be {wanted.__name__}, got {value!r}")
    if name == "status" and value not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}, got {value!r}")
    if name == "tags" and not all(type(t) is str and t for t in value):
        raise ValueError(f"tags must be non-empty strings, got {value!r}")
    if name == "loaded_by" and not all(type(t) is str and t for t in value):
        raise ValueError(f"loaded_by must be class ids, got {value!r}")
    if name == "fp" and not all(k in FINGERPRINTED and type(v) is str for k, v in value.items()):
        raise ValueError(f"fp must map {', '.join(FINGERPRINTED)} to hashes, got {value!r}")
    if name == "ignore_warnings" and not all(k in WARNING_KINDS for k in value):
        raise ValueError(f"ignore_warnings must be some of {', '.join(WARNING_KINDS)}, "
                         f"got {value!r}")


def parse_tags(value):
    """Tags from `a, b` text or a list: trimmed, no blanks or repeats; None when empty."""
    items = value.split(",") if isinstance(value, str) else value
    tags = list(dict.fromkeys(t.strip() for t in items if t.strip()))
    return tags or None


def clean_value(name, value):
    """A viewer/CLI value as its note type: tags parsed, empty text or list clears (None)."""
    if name == "tags" and value is not None:
        return parse_tags(value)
    return None if value in ("", []) else value  # ignore_warnings: a list, [] clears


def human_author(repo=None):
    """Who the human here is, by a real name so co-workers tell each other apart (DESIGN 7d):
    $CODERSTANDING_USER, else the repo's `git config user.name`, else the OS user. The one
    place that answers it (notes, tours and comments written from the viewer)."""
    name = os.environ.get(USER_ENV, "").strip()
    if not name and repo:
        try:
            name = run_quiet(["git", *GIT_OFFLINE, "-C", str(repo), "config", "user.name"],
                                  capture_output=True, text=True, timeout=5,
                                  env={**os.environ, **GIT_OFFLINE_ENV}).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            name = ""
    if not name:
        try:
            name = getpass.getuser()
        except (OSError, KeyError):
            name = ""
    return name or HUMAN_AUTHOR


class Notes:
    def __init__(self):
        self.entries = {}  # (key, by) -> {field: value}
        self.ai = set()  # (key, by) of AI-written entries
        self._authors = {}  # key -> set of authors with an entry
        self.rules = []  # the app's warning rules (apps.py sets them on load; not in notes.jsonl)
        self.layers = []  # the app's architecture rules (same)
        self.levels = None  # the app's saved {count, names} (same; views.level_scale)
        self.loaded_rules = []  # the app's "loaded by" pattern rules (same; checks.parse_loaded_rule)

    def is_ai(self, key, by):
        return (key, by) in self.ai

    def authors(self, key):
        """Authors of a key in read priority: humans (`you` first), then AI authors."""
        return sorted(self._authors.get(key, ()),
                      key=lambda by: (self.is_ai(key, by), by != HUMAN_AUTHOR, by))

    def get(self, key, name):
        """(value, by) of a field, a human value first; (None, None) if unset."""
        for by in self.authors(key):
            value = self.entries[(key, by)].get(name)
            if value is not None:
                return value, by
        return None, None

    def human_has(self, key, name):
        return any(not self.is_ai(key, by) and self.entries[(key, by)].get(name) is not None
                   for by in self._authors.get(key, ()))

    def value(self, key, name, default=None):
        value, _ = self.get(key, name)
        return default if value is None else value

    def loaders(self, graph):
        """class id -> [{from, how, source, by, ai, rule}] of the classes that load it by name
        (DESIGN 9b): the `loaded_by` note field, then the app's pattern rules (`source` is
        `note` or `rule`). Ends missing from the graph are skipped."""
        found = {}

        def add(target, loader, how, source, by=None, rule=None):
            links = found.setdefault(target, [])
            if loader != target and loader in graph.nodes and all(l["from"] != loader for l in links):
                links.append({"from": loader, "how": how or "", "source": source, "by": by,
                              "ai": by is not None and self.is_ai(node_key(target), by),
                              "rule": rule})

        for key in self.keys():
            target = key[len(NODE_PREFIX):]
            ids, by = self.get(key, "loaded_by") if key.startswith(NODE_PREFIX) else (None, None)
            if ids and target in graph.nodes:
                for loader in ids:
                    add(target, loader, self.value(key, "loaded_how"), "note", by)
        for rule in (r for r in self.loaded_rules if r.get("loaded_by") and r.get("files")):
            matches = glob_regex(rule["files"]).match
            for node in graph.nodes.values():
                if node.kind != "external" and node.file and matches(node.file):
                    add(node.id, rule["loaded_by"], rule.get("how"), "rule", rule=rule["files"])
        return {target: links for target, links in found.items() if links}

    def ignored_warnings(self, node_id):
        """Warning kinds the node's notes say to ignore."""
        return self.value(node_key(node_id), "ignore_warnings", [])

    def domain_of(self, node_id):
        return self.value(node_key(node_id), "domain")

    def tags_of(self, node_id):
        return self.value(node_key(node_id), "tags", [])

    def has(self, key, name):
        return self.get(key, name)[0] is not None

    def set(self, key, by, fields, ai=None, graph=None):
        """Write fields as `by` (ai: an AI draft; default: by is AI_AUTHOR). None clears a
        field. Returns the fields skipped because a human set them. With a graph, the written
        text fields remember the fingerprint of the code now (stale marks, DESIGN 10)."""
        if not isinstance(by, str) or not by.strip():
            raise ValueError("by must be an author name")
        ai = by == AI_AUTHOR if ai is None else ai
        skipped = []
        for name, value in fields.items():
            check_value(name, value)
            if ai and self.human_has(key, name):
                skipped.append(name)
                continue
            self.put(key, by, ai, {name: value})
            if name in FINGERPRINTED and value is not None:
                self.stamp(key, by, [name], graph)
            if not ai:  # a human value replaces the AI drafts and other people's older value
                for other in self.authors(key):
                    if other != by:
                        self.put(key, other, self.is_ai(key, other), {name: None})
        return skipped

    def stamp(self, key, by, names, graph):
        """Remember the code's current fingerprint for `by`'s text fields (none known: forget)."""
        current = graph.fingerprints.get(key) if graph else None
        fp = dict(self.entries.get((key, by), {}).get("fp", {}))
        for name in names:
            if current:
                fp[name] = current
            else:
                fp.pop(name, None)
        self.put(key, by, self.is_ai(key, by), {"fp": fp or None})

    def confirm(self, key, graph, names=FINGERPRINTED):
        """Mark the key's existing values "still true" for the code as it is now. Returns the
        fields refreshed."""
        done = []
        for by in self.authors(key):
            have = [n for n in names if self.entries[(key, by)].get(n) is not None]
            self.stamp(key, by, have, graph)
            done += [n for n in have if n not in done]
        return done

    def stale_fields(self, key, graph, names=FINGERPRINTED):
        """The fields whose shown value was written for other code than is there now. A value
        with no fingerprint (old note) or code with none is unknown, not stale."""
        current = graph.fingerprints.get(key)
        stale = []
        for name in names:
            value, by = self.get(key, name)
            written = self.entries[(key, by)].get("fp", {}).get(name) if value is not None else None
            if current and written and written != current:
                stale.append(name)
        return stale

    def stale_notes(self, graph):
        """[(key, stale fields)] for every note key with stale values, sorted by key."""
        return [(key, fields) for key in self.keys() if (fields := self.stale_fields(key, graph))]

    def put(self, key, by, ai, fields):
        """Store fields in one author's entry as they are (None removes a field)."""
        entry = self.entries.setdefault((key, by), {})
        self._authors.setdefault(key, set()).add(by)
        (self.ai.add if ai else self.ai.discard)((key, by))
        for name, value in fields.items():
            if value is None:
                entry.pop(name, None)
            else:
                entry[name] = value
        for name in [n for n in entry.get("fp", {}) if n not in entry]:
            del entry["fp"][name]  # the field is gone: so is its fingerprint
        if not entry.get("fp"):
            entry.pop("fp", None)
        if not entry:
            del self.entries[(key, by)]
            self.ai.discard((key, by))
            self._authors[key].discard(by)

    def clear_field(self, name, ai_only=True):
        """Remove a field from every author's entry (only AI drafts when ai_only). Returns the
        removed as notes.jsonl lines
        ({"key", name, "by", "ai"?}, sorted), so the caller can back them up."""
        removed = [{"key": key, name: fields[name], "by": by, **({"ai": True} if self.is_ai(key, by) else {})}
                   for (key, by), fields in sorted(self.entries.items())
                   if name in fields and not (ai_only and not self.is_ai(key, by))]
        for line in removed:
            self.put(line["key"], line["by"], line.get("ai", False), {name: None})
        return removed

    def keys(self):
        return sorted({key for key, _ in self.entries})

    def lost(self, graph):
        """Keys that point at a node or edge end the graph no longer has (never deleted)."""
        return [key for key in self.keys() if not self.key_exists(key, graph)]

    def key_exists(self, key, graph):
        """True if the key names a graph node, a domain card, or an edge between two of them."""
        known = set(graph.nodes) | {domain_id(d) for d in self.domains()}
        if key.startswith(NODE_PREFIX):
            node_id = key[len(NODE_PREFIX):]
            return node_id in known or is_function(graph, node_id)
        if key.startswith(EDGE_PREFIX) and ARROW in key:
            from_id, to = key[len(EDGE_PREFIX):].split(ARROW, 1)
            return from_id in known and to in known
        return False

    def tags(self):
        return sorted({t for e in self.entries.values() for t in e.get("tags", [])})

    def domains(self):
        return sorted({e["domain"] for e in self.entries.values() if e.get("domain")})

    def to_lines(self):
        lines = []
        for (key, by), fields in sorted(self.entries.items()):
            ai = {"ai": True} if self.is_ai(key, by) else {}
            lines.append(json.dumps({"key": key, **dict(sorted(fields.items())), "by": by, **ai},
                                    ensure_ascii=False))
        return lines


def is_function(graph, function_id):
    """True for `<class>#<function>` naming a function of a graph class."""
    node_id, sep, name = function_id.rpartition(FUNCTION_SEP)
    node = graph.nodes.get(node_id) if sep else None
    if node is None:
        return False
    if name in function_names(node, ()):
        return True
    calls = [c for e in graph.edges.values() if e.from_id == node_id for c in e.calls]
    return name in function_names(node, calls)  # a caller only seen in calls (`<init>`)


def load_notes(path):
    notes = Notes()
    path = Path(path)
    if not path.exists():
        return notes
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        data = json.loads(line)
        key, by = data.pop("key"), data.pop("by", HUMAN_AUTHOR)
        ai = data.pop("ai", by == AI_AUTHOR)  # old lines: `claude` lines are AI drafts
        if not isinstance(by, str) or not isinstance(ai, bool):
            raise ValueError(f"{path}:{number}: by must be a name and ai true/false")
        for name, value in data.items():
            check_value(name, value)
        notes.put(key, by, ai, data)
    return notes


def save_notes(notes, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = notes.to_lines()
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
