"""JS/TS project config (docs/DESIGN.md 4.4b): import path aliases, read once per scan.

tsconfig/jsconfig files (JSONC: comments and trailing commas) give `baseUrl`, `paths` and
`extends`; package.json `name`s give workspace packages (`@example/shared` -> its folder).
`PathMap.bases` turns an import path that is not relative into repo-relative candidates the
resolver tries with the language's path suffixes. No tree-sitter.
"""

import json
import posixpath
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .filewalk import list_files

CONFIG_PATTERNS = ("tsconfig*.json", "jsconfig*.json")
MAIN_CONFIGS = ("tsconfig.json", "jsconfig.json")  # win over tsconfig.app.json & co in one folder
PACKAGE_ENTRY_FIELDS = ("source", "types", "typings", "module", "main")
BUILD_DIRS = ("dist/", "build/", "lib/", "out/")  # built output: the source is often in src/
DECLARATION_EXT = ".d.ts"


# --- JSONC ---------------------------------------------------------------------------------

def strip_jsonc(text):
    """Plain JSON from JSONC: `//` and `/* */` comments and trailing commas removed; strings
    are kept as they are."""
    out, i, n = [], 0, len(text)
    while i < n:
        char = text[i]
        if char == '"':
            end = i + 1
            while end < n and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(text[i:end + 1])
            i = end + 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            if char in "}]":
                drop_trailing_comma(out)
            out.append(char)
            i += 1
    return "".join(out)


def drop_trailing_comma(out):
    k = len(out) - 1
    while k >= 0 and out[k].isspace():
        k -= 1
    if k >= 0 and out[k] == ",":
        del out[k]


def load_jsonc(text):
    data = json.loads(strip_jsonc(text))
    return data if isinstance(data, dict) else {}


# --- tsconfig files ------------------------------------------------------------------------

def in_repo(rel):
    return rel not in ("", ".") and not rel.startswith("../") and rel != ".."


class ConfigReader:
    """Reads tsconfig files with their `extends` chain; each one once per scan."""

    def __init__(self, root):
        self.root = Path(root)
        self.options_by_file = {}
        self.problems = Counter()  # "unreadable", "missing extends"

    def options(self, rel):
        """{"baseUrl" / "paths": (value, folder of the config that set it)}, extends applied
        (a child's option replaces its parent's, as in TypeScript)."""
        if rel in self.options_by_file:
            return self.options_by_file[rel]
        self.options_by_file[rel] = {}  # guards extends loops
        try:
            data = load_jsonc((self.root / rel).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            self.problems["unreadable"] += 1
            return {}
        folder = posixpath.dirname(rel)
        merged = {}
        extends = data.get("extends")
        for spec in [extends] if isinstance(extends, str) else (extends or []):
            parent = self.find_extends(spec, folder) if isinstance(spec, str) else None
            if parent:
                merged.update(self.options(parent))
            else:
                self.problems["missing extends"] += 1
        compiler = data.get("compilerOptions")
        for key in ("baseUrl", "paths"):
            if isinstance(compiler, dict) and key in compiler:
                merged[key] = (compiler[key], folder)
        self.options_by_file[rel] = merged
        return merged

    def find_extends(self, spec, folder):
        if spec.startswith(("./", "../")):
            starts = [posixpath.join(folder, spec)]
        else:  # a package: node_modules/<spec> in this folder or above
            starts, here = [], folder
            while True:
                starts.append(posixpath.join(here, "node_modules", spec))
                if here in ("", "."):
                    break
                here = posixpath.dirname(here)
        for start in starts:
            for suffix in ("", ".json", "/tsconfig.json"):
                rel = posixpath.normpath(start + suffix)
                if in_repo(rel) and (self.root / rel).is_file():
                    return rel
        return None


@dataclass
class Scope:
    """The aliases of one folder's tsconfig files (they apply to every file below it)."""
    folder: str
    base_url: str = None  # repo-relative, or None
    patterns: list = field(default_factory=list)  # (prefix, suffix, wildcard, targets)

    def match(self, written):
        """Targets of the best pattern (longest prefix, as TypeScript picks), or None."""
        for prefix, suffix, wildcard, targets in self.patterns:
            if not wildcard and written == prefix:
                return list(targets)
            if wildcard and written.startswith(prefix) and written.endswith(suffix) and \
                    len(written) >= len(prefix) + len(suffix):
                star = written[len(prefix):len(written) - len(suffix)]
                return [t.replace("*", star, 1) for t in targets]
        return None


def resolved_dir(value, folder):
    return posixpath.normpath(posixpath.join(folder, value)) if isinstance(value, str) else None


def make_scope(folder, option_sets):
    """option_sets: each config's options, the winning config first."""
    scope, seen = Scope(folder), {}
    for options in option_sets:
        base_url = resolved_dir(*options["baseUrl"]) if "baseUrl" in options else None
        if scope.base_url is None:
            scope.base_url = base_url
        paths, origin = options.get("paths", (None, folder))
        targets_dir = base_url if base_url is not None else origin
        for key, targets in (paths.items() if isinstance(paths, dict) else []):
            if key in seen or not isinstance(targets, list):
                continue
            seen[key] = [posixpath.normpath(posixpath.join(targets_dir, t))
                         for t in targets if isinstance(t, str)]
    for key, targets in seen.items():
        prefix, wildcard, suffix = key.partition("*")
        scope.patterns.append((prefix, suffix, bool(wildcard), targets))
    scope.patterns.sort(key=lambda p: -len(p[0]))
    return scope


def config_order(rel):
    name = posixpath.basename(rel)
    return (name not in MAIN_CONFIGS, MAIN_CONFIGS.index(name) if name in MAIN_CONFIGS else 0, name)


# --- workspace packages --------------------------------------------------------------------

def package_entries(root, rel):
    """(name, [entry bases]) of a package.json, or None without a name."""
    try:
        data = json.loads((Path(root) / rel).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name:
        return None
    folder = posixpath.dirname(rel)
    entries = []
    for key in PACKAGE_ENTRY_FIELDS:
        value = data.get(key)
        if isinstance(value, str):
            entries += entry_bases(folder, value)
    entries += [posixpath.join(folder, "src/index"), posixpath.join(folder, "index")]
    return name, list(dict.fromkeys(posixpath.normpath(e) for e in entries))


def entry_bases(folder, value):
    value = posixpath.normpath(value)
    if value.endswith(DECLARATION_EXT):
        value = value[:-len(DECLARATION_EXT)]
    found = [posixpath.join(folder, value)]
    for build in BUILD_DIRS:
        if value.startswith(build):
            found.append(posixpath.join(folder, "src", posixpath.splitext(value[len(build):])[0]))
    return found


# --- the map -------------------------------------------------------------------------------

@dataclass
class PathMap:
    scopes: dict = field(default_factory=dict)  # folder -> Scope
    packages: dict = field(default_factory=dict)  # package name -> (folder, [entry bases])
    info: dict = field(default_factory=dict)  # counts for `coderstanding diagnose`
    _scope_of: dict = field(default_factory=dict)

    def scope_for(self, from_file):
        folder = posixpath.dirname(from_file)
        if folder not in self._scope_of:
            here = folder
            while here not in self.scopes and here not in ("", "."):
                here = posixpath.dirname(here)
            self._scope_of[folder] = self.scopes.get(here) or self.scopes.get("")
        return self._scope_of[folder]

    def bases(self, written, from_file):
        """([repo-relative bases to try], matched an alias) for an import path as written."""
        if written.startswith((".", "/")):
            return [], False
        found, matched = [], False
        scope = self.scope_for(from_file)
        if scope is not None:
            targets = scope.match(written)
            if targets is not None:
                found, matched = targets, True
            if scope.base_url is not None:
                found.append(posixpath.normpath(posixpath.join(scope.base_url, written)))
        package = self.package_bases(written)
        return found + package, matched or bool(package)

    def package_bases(self, written):
        for name, (folder, entries) in self.packages.items():
            if written == name:
                return list(entries)
            if written.startswith(name + "/"):
                rest = written[len(name) + 1:]
                return [posixpath.join(folder, rest), posixpath.join(folder, "src", rest)]
        return []


def read_path_map(root, skip_dirs):
    """The PathMap of every tsconfig/jsconfig and package.json in the repo (None if none)."""
    configs = list_files(root, CONFIG_PATTERNS, skip_dirs)
    package_files = list_files(root, ["package.json"], skip_dirs)
    if not configs and not package_files:
        return None
    reader = ConfigReader(root)
    by_folder = {}
    for rel in sorted(configs, key=config_order):
        by_folder.setdefault(posixpath.dirname(rel), []).append(reader.options(rel))
    path_map = PathMap()
    for folder, option_sets in by_folder.items():
        path_map.scopes[folder] = make_scope(folder, option_sets)
    for rel in package_files:
        entry = package_entries(root, rel)
        if entry:
            path_map.packages[entry[0]] = (posixpath.dirname(rel), entry[1])
    path_map.info = map_info(configs, reader, path_map)
    return path_map


def map_info(configs, reader, path_map):
    options = [reader.options(rel) for rel in configs]
    return {
        "configs": len(configs),
        "with_paths": sum(1 for o in options if "paths" in o),
        "with_base_url": sum(1 for o in options if "baseUrl" in o),
        "patterns": sorted({p[0] + ("*" + p[1] if p[2] else "")
                            for s in path_map.scopes.values() for p in s.patterns}),
        "packages": len(path_map.packages),
        "problems": dict(reader.problems),
    }
