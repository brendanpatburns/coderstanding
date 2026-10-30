"""Comments (docs/DESIGN.md 7d): threads on any item, e.g. a tour or one of its steps.

A comment belongs to a target: `<kind>:<id>` (`tour:walk`) or `<kind>:<id>#<part>`
(`tour:walk#s1`, a step key), optionally narrowed to a code line (stored as an offset inside a
class or function, so it follows edits like tour code) or a quoted text snippet. Replies point at
their `parent`; a thread (the first comment and its replies) is resolved or open.

Storage sits next to the item, committed with it: `<data>/<folder>/<id>.comments.jsonl`, one
comment per line, sorted by (thread, time). Every change stamps `changed`, so two people's files
merged by git (the folder's `.gitattributes` asks for `merge=union`) may hold one comment twice:
reading keeps the newest copy. Deleting keeps the line (`deleted`, text gone) for the same reason.
Comments whose target is gone (a deleted step) are listed as lost, never dropped.

Kinds plug in with `add_kind(prefix, folder, owner)`: the owner answers `comment_exists(rest)`
and `comment_anchor(rest)` (the class/function a line comment is anchored on, or None).
"""

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

SUFFIX = ".comments.jsonl"
GITATTRIBUTES = f"*{SUFFIX} merge=union\n"  # appended lines of two people merge without conflicts
PART_SEP = "#"
MAX_TEXT = 20000


def now():
    """UTC time to the millisecond: comments made in one second still sort in order."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def stamp(comment):
    """The comment's last change: the newest copy of a comment wins after a merge."""
    return (comment.get("changed") or comment["created"], json.dumps(comment, sort_keys=True))


def matches(target, prefix):
    """`tour:walk` covers itself and its parts (`tour:walk#s1`); `tour:` covers every tour."""
    if not prefix:
        return True
    if prefix.endswith((":", PART_SEP)):
        return target.startswith(prefix)
    return target == prefix or target.startswith(prefix + PART_SEP)


def clean_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a comment needs text")
    if len(text) > MAX_TEXT:
        raise ValueError(f"a comment has at most {MAX_TEXT} characters")
    return text.strip()


class Comments:
    """Every comment of one app's data folder, by target."""

    def __init__(self, data_dir, locate=None):
        self.data = Path(data_dir)
        self.locate = locate  # at -> (path, first line) of a class or function today, or None
        self.kinds = {}  # prefix -> (folder, owner)

    def add_kind(self, prefix, folder, owner):
        self.kinds[prefix] = (folder, owner)

    # targets and files
    def split(self, target):
        """(kind, item id, part or None) of a target; unknown kinds are refused."""
        kind, sep, rest = str(target).partition(":")
        if not sep or kind not in self.kinds or not rest:
            raise ValueError(f"bad comment target {target!r}: use one of "
                             + ", ".join(f"{k}:<id>[#part]" for k in sorted(self.kinds)))
        item, _, part = rest.partition(PART_SEP)
        if not item or "/" in item or "\\" in item or item.startswith("."):
            raise ValueError(f"bad comment target {target!r}")
        return kind, item, part or None

    def file_of(self, target):
        kind, item, _ = self.split(target)
        return self.data / self.kinds[kind][0] / f"{item}{SUFFIX}"

    def files(self, prefix=""):
        """The files that may hold comments under `prefix`."""
        kind, sep, rest = prefix.partition(":")
        if sep and rest and kind in self.kinds:
            return [self.file_of(prefix)]
        folders = [self.kinds[kind][0]] if kind in self.kinds else [f for f, _ in self.kinds.values()]
        return sorted(p for f in folders for p in (self.data / f).glob(f"*{SUFFIX}"))

    # reading and writing one file
    @staticmethod
    def read_file(path):
        """{id: comment} of one file; a comment seen twice (a merge) keeps its newest copy."""
        found = {}
        if not path.is_file():
            return found
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.startswith(("<<<<<<<", "=======", ">>>>>>>")):
                continue
            comment = json.loads(line)
            if not isinstance(comment, dict) or not {"id", "target", "by", "created"} <= set(comment):
                raise ValueError(f"{path}:{number}: a comment needs id, target, by and created")
            old = found.get(comment["id"])
            if old is None or stamp(comment) > stamp(old):
                found[comment["id"]] = comment
        return found

    @staticmethod
    def write_file(path, comments):
        path.parent.mkdir(parents=True, exist_ok=True)
        attributes = path.parent / ".gitattributes"
        if not attributes.exists():
            attributes.write_text(GITATTRIBUTES, encoding="utf-8")
        lines = [json.dumps(c, ensure_ascii=False, sort_keys=True) for c in sort_comments(comments)]
        path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")

    def all(self, prefix=""):
        """{id: comment} under `prefix`."""
        found = {}
        for path in self.files(prefix):
            found.update(self.read_file(path))
        return {i: c for i, c in found.items() if matches(c["target"], prefix)}

    def find(self, comment_id):
        """(file, its comments, the comment) for an id, else KeyError."""
        for path in self.files():
            comments = self.read_file(path)
            if comment_id in comments:
                return path, comments, comments[comment_id]
        raise KeyError(f"no comment {comment_id}")

    # changes
    def add(self, target, text, by, ai=False, parent=None, line=None, quote=None):
        """A new comment (a reply when `parent` is given: it joins the parent's thread and
        target). `line` is today's line number inside the target's code anchor."""
        text = clean_text(text)
        if parent:
            path, comments, above = self.find(parent)
            target = above["target"]
        else:
            path = self.file_of(target)
            comments = self.read_file(path)
        if not self.exists(target):
            raise ValueError(f"no {target} to comment on")
        time = now()
        comment = {"id": new_id(comments), "target": target, "by": by, "created": time,
                   "changed": time, "text": text}
        if ai:
            comment["ai"] = True
        if parent:
            comment["parent"] = parent
        if line is not None:
            comment["anchor"] = self.anchor_for(target, line)
        if quote:
            if not isinstance(quote, str):
                raise ValueError("quote must be text")
            comment["quote"] = quote.strip()[:500]
        comments[comment["id"]] = comment
        self.write_file(path, comments.values())
        return comment

    def anchor_for(self, target, line):
        """{at, off}: a line number of today as an offset inside the target's anchor."""
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise ValueError("line must be a line number")
        kind, item, part = self.split(target)
        at = self.kinds[kind][1].comment_anchor(item + (f"{PART_SEP}{part}" if part else ""))
        found = self.locate(at) if at and self.locate else None
        if not found:
            raise ValueError(f"{target} has no code to put a line comment on")
        if line < found[1]:
            raise ValueError(f"line {line} is above {at} (starts at line {found[1]})")
        return {"at": at, "off": line - found[1]}

    def change(self, comment_id, edit):
        """Run edit(comment, comments) on one comment and save; returns the comment."""
        path, comments, comment = self.find(comment_id)
        edit(comment, comments)
        comment["changed"] = now()
        self.write_file(path, comments.values())
        return comment

    def edit(self, comment_id, text, by):
        def edit(comment, _):
            own(comment, by)
            comment["text"], comment["edited"] = clean_text(text), now()
        return self.change(comment_id, edit)

    def resolve(self, comment_id, by, done=True):
        """Resolve (or reopen) the thread the comment is in."""
        path, comments, comment = self.find(comment_id)
        root = thread_root(comment, comments)
        def edit(target, _):
            if done:
                target["resolved"] = {"by": by, "at": now()}
            else:
                target.pop("resolved", None)
        return self.change(root["id"], edit)

    def delete(self, comment_id, by):
        def edit(comment, _):
            own(comment, by)
            comment.pop("text", None)
            comment["deleted"] = now()
        return self.change(comment_id, edit)

    # views
    def exists(self, target):
        kind, item, part = self.split(target)
        return bool(self.kinds[kind][1].comment_exists(item + (f"{PART_SEP}{part}" if part else "")))

    def line_of(self, comment):
        """(path, today's line) of a line comment; (None, None) when its anchor is gone."""
        anchor = comment.get("anchor")
        found = self.locate(anchor["at"]) if anchor and self.locate else None
        return (found[0], found[1] + anchor["off"]) if found else (None, None)

    def comment_json(self, comment, comments, lost_targets):
        root = thread_root(comment, comments)
        path, line = self.line_of(comment) if comment.get("anchor") else (None, None)
        return {"id": comment["id"], "target": comment["target"], "by": comment["by"],
                "ai": bool(comment.get("ai")), "created": comment["created"],
                "edited": comment.get("edited"), "text": comment.get("text", ""),
                "parent": comment.get("parent"), "thread": root["id"],
                "anchor": comment.get("anchor"), "path": path, "line": line,
                "line_lost": bool(comment.get("anchor")) and line is None,
                "quote": comment.get("quote"), "resolved": root.get("resolved"),
                "deleted": bool(comment.get("deleted")),
                "lost": comment["target"] in lost_targets}

    def list_json(self, prefix="", resolved=True):
        """Every comment under `prefix` in (thread, time) order; deleted comments only while
        they still have replies; resolved threads only with `resolved`."""
        comments = self.all(prefix)
        lost = {c["target"] for c in comments.values() if not self.exists(c["target"])}
        shown = [c for c in sort_comments(comments.values()) if visible(c, comments)
                 and (resolved or not thread_root(c, comments).get("resolved"))]
        return [self.comment_json(c, comments, lost) for c in shown]

    def open_counts(self, prefix):
        """{target: open threads} under `prefix` (a thread counts on its first comment's target)."""
        counts = {}
        comments = self.all(prefix)
        for c in comments.values():
            if not c.get("parent") and not c.get("resolved") and visible(c, comments):
                counts[c["target"]] = counts.get(c["target"], 0) + 1
        return counts

    def lost(self, prefix=""):
        """{target: comments} of targets that are gone (a deleted step or item)."""
        counts = {}
        for c in self.all(prefix).values():
            if not c.get("deleted") and not self.exists(c["target"]):
                counts[c["target"]] = counts.get(c["target"], 0) + 1
        return dict(sorted(counts.items()))


def new_id(taken):
    while True:
        key = secrets.token_hex(4)
        if key not in taken:
            return key


def own(comment, by):
    if comment["by"] != by:
        raise PermissionError(f"comment {comment['id']} is {comment['by']}'s, not {by}'s")


def thread_root(comment, comments):
    seen = set()
    while comment.get("parent") in comments and comment["id"] not in seen:
        seen.add(comment["id"])
        comment = comments[comment["parent"]]
    return comment


def visible(comment, comments):
    """A deleted comment stays visible (as "deleted") while a live reply hangs under it."""
    if not comment.get("deleted"):
        return True
    return any(c.get("parent") == comment["id"] and visible(c, comments) for c in comments.values())


def sort_comments(comments):
    """(thread, time): each thread's first comment by target and time, its replies after it."""
    comments = list(comments)
    by_id = {c["id"]: c for c in comments}
    def key(c):
        root = thread_root(c, by_id)
        return (root["target"], root["created"], root["id"], c is not root, c["created"], c["id"])
    return sorted(comments, key=key)
