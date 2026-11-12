"""Names index: which names appear on which lines of every repo file, code and not.

Built at scan time over the code files the language modules read plus docs, compose files,
Dockerfiles, `.env` files, shell scripts and CI/config files (the file walk's skip rules hold).
Per file it keeps the kind (src / test / doc / deploy / script / config), every name on each
line, string literals (code and scripts) and env var reads and sets. Saved as `names.json`
beside graph.json (graph.json itself is unchanged); per-file results live in the scan cache,
so a rescan only re-reads changed files. Line text is read from the repo when asked for.

Queries: `uses` (every line a name is on), `mentions` (by kind, for impact), `env_vars`,
`dupes`.
"""

import json
import posixpath
import re
from collections import defaultdict
from pathlib import Path

from .cache import file_hash
from .filewalk import DEFAULT_SKIP_DIRS, list_files, matches
from .testcode import is_test_path
from .vendor import is_skipped_by_name, is_wide

INDEX_FILE = "names.json"
INDEX_VERSION = 2  # bump when the per-file entry changes (also renames its cache entries)
CACHE_MODULE = f"names{INDEX_VERSION}"
MAX_BYTES = 512_000  # bigger files (data dumps, lock files) are left out

KINDS = ("src", "test", "doc", "deploy", "script", "config")
DOC_PATTERNS = ("*.md", "*.rst", "*.adoc")
DEPLOY_PATTERNS = ("Dockerfile", "Dockerfile.*", "*.dockerfile", "Containerfile", "*compose*.yml",
                   "*compose*.yaml", ".env", ".env.*", "*.env", "Procfile")
SCRIPT_PATTERNS = ("*.sh", "*.bash", "*.zsh", "*.ps1", "*.bat", "*.cmd")
CONFIG_PATTERNS = ("*.yml", "*.yaml", "*.toml", "*.ini", "*.cfg", "*.conf", "*.json", "*.txt",
                   "Makefile", "*.mk", "*.godot")
NON_CODE_PATTERNS = DOC_PATTERNS + DEPLOY_PATTERNS + SCRIPT_PATTERNS + CONFIG_PATTERNS

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ENV_NAME = r"([A-Za-z_][A-Za-z0-9_]*)"
CAPS_NAME = r"([A-Z_][A-Z0-9_]*)"
QUOTED = r"""\s*["']""" + ENV_NAME + r"""["']"""

# Code reading or setting env vars (GDScript, Python, JS/TS, C#, Java/Kotlin, C, Go, PHP, Rust).
# A role ending in "?" names a constant holding the var's name (`const ENV := "X"` in the file).
CODE_SET_CALLS = (r"(?:set_environment|putenv|setenv|SetEnvironmentVariable|Setenv|"
                  r"environ\.setdefault|env::set_var)\(")
CODE_READ_CALLS = (r"(?:get_environment|has_environment|getenv|GetEnvironmentVariable|Getenv|"
                   r"LookupEnv|environ\.get|environ\.pop|env::var|ENV\.fetch)\(")
BARE = r"\s*([A-Za-z_]\w*)\s*[,)]"
CODE_ENV = (
    ("set", re.compile(CODE_SET_CALLS + QUOTED)),
    ("set?", re.compile(CODE_SET_CALLS + BARE)),
    ("set", re.compile(r"environ\[" + QUOTED + r"\s*\]\s*=(?!=)")),
    ("set", re.compile(r"process\.env\.([A-Za-z_]\w*)\s*=(?!=)")),
    ("read", re.compile(CODE_READ_CALLS + QUOTED)),
    ("read?", re.compile(CODE_READ_CALLS + BARE)),
    ("read", re.compile(r"environ\[" + QUOTED + r"\s*\]")),
    ("read", re.compile(r"(?:process|import\.meta)\.env\." + ENV_NAME)),
    ("read", re.compile(r"process\.env\[" + QUOTED)),
)
CONST_STRING = re.compile(r"\b([A-Za-z_]\w*)\s*(?::\s*\w+\s*)?:?=\s*[\"']([A-Za-z_]\w*)[\"']")
ASSIGNED = re.compile(CAPS_NAME + "=")
SHELL_ENV = (
    ("set", re.compile(r"^\s*(?:export|set|setx|\$env:)\s*" + CAPS_NAME + r"\b")),
    ("set", re.compile(r"(?:\s-e|--env)\s+[\"']?" + CAPS_NAME + r"\b")),
    # `X=1 Y="a b" command` (also before a `\` line break): every assigned name
    ("set", re.compile(r"^\s*((?:[A-Z_][A-Z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S*)\s+)+)(?=[a-z./\\])")),
    ("read", re.compile(r"\$\{?" + CAPS_NAME)),
    ("read", re.compile(r"%" + CAPS_NAME + "%")),
)
DOCKERFILE_ENV = (
    ("set", re.compile(r"^\s*(?:ENV|ARG)\s+" + CAPS_NAME + r"\b")),
    ("set", re.compile(r"^\s*ENV\s+.*?\s" + CAPS_NAME + "=")),
    ("read", re.compile(r"\$\{?" + CAPS_NAME)),
)
ENV_FILE_ENV = (("set", re.compile(r"^\s*(?:export\s+)?#?\s*" + CAPS_NAME + r"\s*=")),)
YAML_ENV = (
    ("set", re.compile(r"^\s*-?\s*[\"']?" + CAPS_NAME + r"[\"']?\s*[:=]")),
    ("read", re.compile(r"\$\{\{?\s*(?:env\.|vars\.|secrets\.)?" + CAPS_NAME)),
    ("read", re.compile(r"\$\$\{?" + CAPS_NAME)),  # compose: the container's shell reads it
)

# Env vars every system has: never reported as "not set in deploy".
SYSTEM_VARS = {"HOME", "PATH", "USER", "USERNAME", "SHELL", "PWD", "TMPDIR", "TEMP", "TMP", "LANG",
               "APPDATA", "LOCALAPPDATA", "USERPROFILE", "PROGRAMFILES", "CI", "DISPLAY", "TERM",
               "WAYLAND_DISPLAY", "HOSTNAME", "EDITOR", "LOGNAME"}

STRING = re.compile(r'"((?:[^"\\\n]|\\.){6,200})"|\'((?:[^\'\\\n]|\\.){6,200})\'')
IMPORT_LINE = re.compile(r"^\s*(?:import\b|from\b\s|#include\b|using\b|extends\b|export\b.*\bfrom\b)|"
                         r"\brequire\(|\bimport\(|\bpreload\(|\bload\(")
MIN_STRING = 6
COMMON_WORD = re.compile(r"[a-z]{1,11}")  # one plain lowercase word: not worth reporting


def file_kind(rel_path, is_code, test_dirs=()):
    """doc / deploy / script by file type (also inside test folders), else test (testcode's
    rule), else src for code and config for the rest."""
    for kind, patterns in (("doc", DOC_PATTERNS), ("deploy", DEPLOY_PATTERNS),
                           ("script", SCRIPT_PATTERNS)):
        if any(matches(rel_path, p) for p in patterns):
            return kind
    if is_test_path(rel_path, test_dirs):
        return "test"
    return "src" if is_code else "config"


def env_rules(rel_path, is_code):
    """The env read/set patterns for this kind of file (by name, not by test folders)."""
    name = posixpath.basename(rel_path)
    if is_script(rel_path):  # a shell script is code to the scan, but reads env the shell way
        return SHELL_ENV
    if is_code:
        return CODE_ENV
    if name.startswith(("Dockerfile", "Containerfile")) or name.endswith(".dockerfile"):
        return DOCKERFILE_ENV
    if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
        return ENV_FILE_ENV
    if name.endswith((".yml", ".yaml")):
        return YAML_ENV
    return ()


def env_hits(rules, line):
    """[(name, role)] for each env read/set on one line (a set wins over a read of the name)."""
    found = {}
    for role, pattern in rules:
        for match in pattern.finditer(line):
            for group in filter(None, match.groups()):
                for name in ASSIGNED.findall(group) if "=" in group else [group]:
                    if not found.get(name, "").startswith("set"):
                        found[name] = role
    return list(found.items())


def literal_wanted(text):
    """Worth reporting: long enough, has letters, not one plain word, and not the code between
    two literals that a stray quote made look like one (`") else "`)."""
    return (len(text) >= MIN_STRING and text == text.strip() and any(c.isalpha() for c in text)
            and text[0] not in ")]},;:+" and text[-1] not in "([{,+"
            and not COMMON_WORD.fullmatch(text))


def read_file(rel_path, text, is_code):
    """One file's entry (cached): {"names": {name: [lines]}, "strings": {...}, "env": [...]}."""
    names, strings, env = defaultdict(list), defaultdict(list), []
    rules = env_rules(rel_path, is_code)
    keep_strings = is_code or is_script(rel_path)
    for number, line in enumerate(text.splitlines(), 1):
        for name in set(NAME.findall(line)):
            names[name].append(number)
        if keep_strings and not IMPORT_LINE.search(line):
            for match in STRING.finditer(line):
                literal = match.group(1) or match.group(2)
                if literal_wanted(literal) and number not in strings[literal][-1:]:
                    strings[literal].append(number)
        env += [[name, number, role] for name, role in env_hits(rules, line)]
    if is_code:
        consts = dict(CONST_STRING.findall(text))
        env = [[consts.get(name), number, role.rstrip("?")] if role.endswith("?") else
               [name, number, role] for name, number, role in env]
        env = [hit for hit in env if hit[0]]
    return {"names": dict(names), "strings": dict(strings), "env": env}


def build_index(root, code_files, skip_dirs=DEFAULT_SKIP_DIRS, test_dirs=(), cache=None, exclude=(), include=()):
    """{"version", "files": {rel_path: entry + "kind"}} for the code files plus every non-code
    file the walk finds. cache: a ScanCache (entries under CACHE_MODULE)."""
    root = Path(root)
    code_files = set(code_files)
    files = {}
    for rel in sorted(code_files | set(list_files(root, NON_CODE_PATTERNS, skip_dirs, include))):
        if is_skipped_by_name(rel, exclude):
            continue
        path = root / rel
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        is_code = rel in code_files
        digest = file_hash(path)
        hit = cache and cache.load(rel, CACHE_MODULE, digest)
        if hit:
            entry = hit[0]
        else:
            text = path.read_bytes().decode("utf-8", "replace")
            if "\0" in text[:4096]:
                continue  # a binary file with a text-like name
            entry = {"skip": True} if is_wide(text) else read_file(rel, text, is_code)
            if cache:
                cache.store(rel, CACHE_MODULE, digest, entry, ())
        if entry.get("skip"):
            continue
        files[rel] = {"kind": file_kind(rel, is_code, test_dirs), **entry}
    return {"version": INDEX_VERSION, "files": files}


def index_path(graph_path):
    return Path(graph_path).parent / INDEX_FILE


def write_index(index, graph_path):
    index_path(graph_path).write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")


def read_index(graph_path):
    """The index saved beside graph.json, or None when missing or from another index version."""
    try:
        index = json.loads(index_path(graph_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return index if index.get("version") == INDEX_VERSION else None


# --- queries ---

def name_parts(name):
    """The identifiers a name is made of (`audio/volume` -> audio, volume)."""
    return NAME.findall(name)


def lookup(index, name, kinds=None):
    """[(rel_path, kind, line)] where every part of the name is on the line, sorted.
    The caller checks the exact text for names with dots or slashes (`exact_lines`)."""
    parts = name_parts(name)
    if not parts:
        return []
    found = []
    for rel, entry in index["files"].items():
        if kinds and entry["kind"] not in kinds:
            continue
        lines = None
        for part in parts:
            got = set(entry["names"].get(part, ()))
            lines = got if lines is None else lines & got
            if not lines:
                break
        found.extend((rel, entry["kind"], line) for line in sorted(lines or ()))
    return sorted(found)


def file_lines(root, rel_paths):
    """rel_path -> its lines (empty when unreadable): read once per file."""
    texts = {}
    for rel in rel_paths:
        try:
            texts[rel] = (Path(root) / rel).read_bytes().decode("utf-8", "replace").splitlines()
        except OSError:
            texts[rel] = []
    return texts


def line_text(texts, rel, line):
    lines = texts.get(rel, [])
    return lines[line - 1].strip() if 0 < line <= len(lines) else ""


DECLARATION = (r"^\s*(?:@\w+\s+)*(?:(?:export|public|private|protected|internal|static|final|"
               r"readonly|const|let|var|val)\s+)*{}\s*(?::[^=]*)?:?=(?!=)")


def declares(text, name):
    """True for a line that defines the name (`const X := 1`, `X = 1`, `export const X = 1`);
    a typed declaration with the type first (`static int X = 1`) counts as a use."""
    return re.match(DECLARATION.format(re.escape(name)), text) is not None


COMMENT_START = ("#", "//", "/*", "*", "--", ";", "<!--")


def is_prose(text, name):
    """True for a line that only talks about the name: a comment-only line, or the name written
    in backticks or on a `help=` / `description=` line (docstrings, help and doc strings)."""
    if text.startswith(COMMENT_START):
        return True
    return f"`{name}" in text or ("help=" in text or "description=" in text)


def uses(index, root, name, kinds=None):
    """[{"file", "kind", "line", "text", "declares", "prose"}] for every line using the name (exact
    text for a compound name like `audio/volume`), sorted by file and line."""
    hits = lookup(index, name, kinds)
    texts = file_lines(root, {rel for rel, _, _ in hits})
    rows = []
    for rel, kind, line in hits:
        text = line_text(texts, rel, line)
        rows.append({"file": rel, "kind": kind, "line": line, "text": text,
                     "declares": declares(text, name), "prose": is_prose(text, name)})
    if len(name_parts(name)) > 1 or name_parts(name)[:1] != [name]:
        rows = [r for r in rows if name in r["text"]]
    return rows


def under_path(rel, prefix):
    return rel.startswith(prefix)


def search_text(index, root, pattern, path=""):
    """[{"file", "kind", "line", "text", "heading"}] for every line of the indexed files matching
    the regex `pattern` (a pattern that is not valid regex is taken literally), sorted by file and
    line. `heading`: the Markdown heading above the line, for docs. `path`: only files under it."""
    try:
        regex = re.compile(pattern)
    except re.error:
        regex = re.compile(re.escape(pattern))
    rows = []
    for rel, entry in sorted(index["files"].items()):
        if not under_path(rel, path):
            continue
        heading = ""
        for number, text in enumerate(file_lines(root, [rel])[rel], 1):
            if entry["kind"] == "doc" and text.startswith("#"):
                heading = text.lstrip("# ").strip()
            if regex.search(text):
                rows.append({"file": rel, "kind": entry["kind"], "line": number,
                             "text": text.strip(), "heading": heading})
    return rows


def mentions(index, name, kinds):
    """{kind: [(rel_path, line)]} of the lines naming `name` in files of these kinds."""
    found = {kind: [] for kind in kinds}
    for rel, kind, line in lookup(index, name, kinds):
        found[kind].append((rel, line))
    return found


def is_script(rel_path):
    return any(matches(rel_path, p) for p in SCRIPT_PATTERNS)


def env_vars(index, prefix=""):
    """[{"name", "read", "set", "doc", "gaps"}] for every env var code reads or deploy files,
    scripts or config set; read/set = [(rel_path, line, kind)], doc = [(rel_path, line)].
    gaps: "not set" (app code reads it, no deploy file, script or config sets it), "not read"
    (set there, nothing reads it), "undocumented" (no doc names it). A script's own variables
    (only scripts read them, nothing deploys them) are left out."""
    found = defaultdict(lambda: {"read": [], "set": []})
    for rel, entry in sorted(index["files"].items()):
        for name, line, role in entry.get("env", ()):
            found[name][role].append((rel, line, entry["kind"]))
    rows = []
    for name, places in sorted(found.items()):
        code_reads = [p for p in places["read"] if p[2] in ("src", "test")]
        deploy_sets = [p for p in places["set"] if p[2] in ("deploy", "script", "config")]
        if not name.startswith(prefix) or not (code_reads or deploy_sets):
            continue
        docs = [(rel, line) for rel, _, line in lookup(index, name, ("doc",))]
        gaps = []
        if any(p[2] == "src" for p in code_reads) and not deploy_sets and name not in SYSTEM_VARS:
            gaps.append("not set")
        if deploy_sets and not places["read"]:
            gaps.append("not read")
        if not docs:
            gaps.append("undocumented")
        rows.append({"name": name, "read": places["read"], "set": places["set"], "doc": docs,
                     "gaps": gaps})
    return rows


def dupes(index, kinds=("src", "script")):
    """[{"text", "files", "places": [(rel_path, line)]}] for string literals found in 2+ files
    of these kinds, most files first, then most uses."""
    found = defaultdict(list)
    for rel, entry in sorted(index["files"].items()):
        if entry["kind"] in kinds:
            for literal, lines in entry.get("strings", {}).items():
                found[literal].extend((rel, line) for line in lines)
    rows = [{"text": text, "files": len({rel for rel, _ in places}), "places": places}
            for text, places in found.items()]
    rows = [r for r in rows if r["files"] > 1]
    return sorted(rows, key=lambda r: (-r["files"], -len(r["places"]), r["text"]))
