"""Why classes look unconnected (`coderstanding diagnose`): a privacy-safe summary.

At scan time `collect` sorts the import paths no file matched into reasons and keeps counts
plus a few raw examples in graph.diagnostics (local, like the rest of graph.json).
`diagnose_text` prints counts only: no file contents, names or paths. With `examples=True` it
adds short anonymised examples (every name replaced by a letter; only `.`/`..`, `index`,
`src`, alias markers and file extensions kept), so the text can go in a public issue.
"""

import posixpath
from collections import Counter
from pathlib import Path

from .stack import npm_package

CLASS_KINDS = ("class", "interface", "enum", "struct", "record")
ALIAS_MARKERS = ("@/", "~/", "#/", "$/", "~", "#", "$")  # common alias spellings, not npm names
EXAMPLES_KEPT = 5
EXAMPLES_SHOWN = 3
NODE_BUILTINS = {
    "assert", "buffer", "child_process", "cluster", "crypto", "dgram", "dns", "events", "fs",
    "fs/promises", "http", "http2", "https", "module", "net", "os", "path", "perf_hooks",
    "process", "querystring", "readline", "stream", "string_decoder", "timers", "tls", "tty",
    "url", "util", "v8", "vm", "worker_threads", "zlib",
}
REASONS = {  # reason -> text after the count; `{keys}`: alias markers with their counts
    "alias-unmatched": "imports starting with an alias marker ({keys}), no tsconfig paths match",
    "alias-missing": "imports matching a tsconfig paths alias or workspace package, no file at the target",
    "bare-folder": "imports starting with a repo folder name, no tsconfig baseUrl or paths cover them",
    "folder-no-index": "imports of folders with no index file",
    "relative-missing": "relative imports of files that are not there (or not scanned)",
    "package-unknown": "imports of packages in no package.json (or aliases from a bundler config)",
}
KEEP_SEGMENTS = {"", ".", "..", "index", "src", "*"}


# --- at scan time --------------------------------------------------------------------------

def collect(root, graph, path_maps):
    """graph.diagnostics from the scan: unresolved import paths by reason, path map counts."""
    folders = {part for node in graph.nodes.values() if node.file
               for part in node.file.split("/")[:-1]}
    deps = {d.get("name") for d in graph.stack.get("deps", [])}
    imports = {}
    for miss in graph.path_misses:
        reason, key = classify(Path(root), miss, folders, deps)
        if not reason:
            continue
        bucket = imports.setdefault(reason, {"count": 0, "files": [], "keys": {}, "examples": []})
        bucket["count"] += 1
        if miss.from_file not in bucket["files"]:
            bucket["files"].append(miss.from_file)
        bucket["keys"][key] = bucket["keys"].get(key, 0) + 1
        if len(bucket["examples"]) < EXAMPLES_KEPT and miss.written not in bucket["examples"]:
            bucket["examples"].append(miss.written)
    for bucket in imports.values():
        bucket["files"] = len(bucket["files"])
    configs = {name: found.info for name, found in path_maps.items() if getattr(found, "info", None)}
    return {"imports": imports, "path_maps": configs} if imports or configs else {}


def classify(root, miss, folders, deps):
    """(reason, key) for a path no file matched; ("", "") when it is fine (a known package)."""
    written = miss.written
    if written.startswith((".", "/")):
        relative = miss.bases[-1]
        return ("folder-no-index" if (root / relative).is_dir() else "relative-missing"), ""
    if miss.matched:
        if any((root / base).is_dir() for base in miss.bases if not base.startswith("..")):
            return "folder-no-index", ""
        return "alias-missing", ""
    marker = next((m for m in ALIAS_MARKERS if written.startswith(m)), "")
    if marker:
        return "alias-unmatched", marker
    head = written.split("/", 1)[0]
    if "/" in written and head in folders and not written.startswith("@"):
        return "bare-folder", ""
    package = npm_package(written)
    if package in deps or written.startswith("node:") or written in NODE_BUILTINS \
            or "://" in written:
        return "", ""
    return "package-unknown", ""


# --- printing ------------------------------------------------------------------------------

class Anonymiser:
    """Replaces every name in a path by a letter (the same name, the same letter)."""

    def __init__(self):
        self.names = {}

    def path(self, written):
        return "/".join(self.segment(s) for s in written.split("/"))

    def segment(self, segment):
        if segment in KEEP_SEGMENTS:
            return segment
        marker = segment[0] if segment[0] in "@~#$" else ""
        stem, ext = posixpath.splitext(segment[len(marker):])
        if stem in KEEP_SEGMENTS:
            return marker + stem + ext
        letter = self.names.setdefault(stem, self.letter(len(self.names)))
        return marker + letter + ext

    @staticmethod
    def letter(index):
        letters = ""
        index += 1
        while index:
            index, rest = divmod(index - 1, 26)
            letters = chr(ord("a") + rest) + letters
        return letters


def class_counts(graph):
    """{lang: [classes, with no lines, unused]} for in-repo classes, interfaces and enums."""
    touched = {i for e in graph.edges.values() if e.from_id != e.to for i in (e.from_id, e.to)}
    counts = {}
    for node in graph.nodes.values():
        if node.kind not in CLASS_KINDS:
            continue
        row = counts.setdefault(node.lang or "?", [0, 0, 0])
        row[0] += 1
        row[1] += node.id not in touched
        row[2] += any(w.get("kind") == "unused" for w in node.warnings)
    return counts


def name_counts(graph):
    """Unresolved names (not import paths) in JS/TS code: (matching a repo class's name, others)."""
    js_refs = [e for n in graph.nodes.values() if n.lang == "javascript"
               for e in n.external_refs.values()]
    paths = {e.to for e in js_refs if "import" in e.detail}  # names imported from them share it
    class_names = {n.name for n in graph.nodes.values() if n.kind in CLASS_KINDS}
    matching, other = Counter(), Counter()
    for edge in js_refs:
        if edge.to not in paths:
            (matching if edge.to.split(".")[-1] in class_names else other)[edge.to] += len(edge.at)
    return sum(matching.values()), sum(other.values())


def config_lines(path_maps):
    lines = []
    for info in path_maps.values():
        problems = ", ".join(f"{n} {kind}" for kind, n in sorted(info.get("problems", {}).items()))
        lines.append(f"tsconfig/jsconfig: {info.get('configs', 0)} files, "
                     f"{info.get('with_paths', 0)} with paths ({len(info.get('patterns', []))} "
                     f"patterns), {info.get('with_base_url', 0)} with baseUrl; "
                     f"{info.get('packages', 0)} package.json names"
                     + (f"; problems: {problems}" if problems else ""))
    return lines


def diagnose_text(graph, examples=False):
    """The summary as text lines (counts only unless examples; no app name either)."""
    out = ["coderstanding diagnose " + ("(anonymised examples)" if examples else "(counts only)")]
    for lang, (total, lonely, unused) in sorted(class_counts(graph).items()):
        out.append(f"classes ({lang}): {total}, {lonely} with no lines at all, {unused} unused")
    path_maps = graph.diagnostics.get("path_maps", {})
    out += config_lines(path_maps) or ["tsconfig/jsconfig: none found"]
    anonymiser = Anonymiser()
    if examples:
        for info in path_maps.values():
            if info.get("patterns"):
                out.append("  alias patterns: " + ", ".join(anonymiser.path(p) for p in info["patterns"]))
    imports = graph.diagnostics.get("imports", {})
    total = sum(b["count"] for b in imports.values())
    out.append(f"unresolved imports: {total}")
    for reason, bucket in sorted(imports.items(), key=lambda kv: -kv[1]["count"]):
        out += reason_lines(reason, bucket, anonymiser if examples else None)
    matching, other = name_counts(graph)
    if matching or other:
        out.append(f"unresolved type/class names in JS/TS: {matching + other}")
        if matching:
            out.append(f"  {matching} match a class name in the repo but are not imported in that "
                       "file (global or ambient declarations?)")
        if other:
            out.append(f"  {other} other outside names (libraries, DOM, globals)")
    return out


def reason_lines(reason, bucket, anonymiser):
    keys = ", ".join(f"`{key}` {n}" for key, n in
                     sorted(bucket["keys"].items(), key=lambda kv: (-kv[1], kv[0])))
    text = REASONS.get(reason, reason).format(keys=keys)
    files = bucket["files"]
    lines = [f"  {bucket['count']} {text} (in {files} file{'s' if files != 1 else ''})"]
    if anonymiser:
        shown = [anonymiser.path(e) for e in bucket["examples"][:EXAMPLES_SHOWN]]
        lines.append("    e.g. " + ", ".join(shown))
    return lines
