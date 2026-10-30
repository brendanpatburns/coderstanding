"""Per-file scan cache: one JSON file per parsed source file in `<repo>/.coderstanding/cache/`.

An entry is valid when its scanner version, module name, path and content hash match, and
every file the parse depended on (see language.record_dependency) still has the hash it had.
JSON (not pickle) so a planted cache folder can never run code.
"""

import dataclasses
import hashlib
import json
import os
from pathlib import Path

from . import language, model

SCANNER_VERSION = 8  # bump to invalidate every cache entry
CACHE_DIR_ENV = "CODERSTANDING_CACHE_DIR"
GITIGNORE_TEXT = "cache/\ngraph.json\n"

_TYPES = {cls.__name__: cls for cls in (
    language.Target, language.Ref, language.Declaration, language.Mark,
    language.FileResult, model.Member)}


def encode(value):
    if dataclasses.is_dataclass(value):
        fields = {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return {"__type": type(value).__name__, **fields}
    if isinstance(value, dict):
        return {"__dict": [[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict) and "__dict" in value:
        return {decode(k): decode(v) for k, v in value["__dict"]}
    if isinstance(value, dict):
        fields = {k: decode(v) for k, v in value.items() if k != "__type"}
        return _TYPES[value["__type"]](**fields)
    return value


def file_hash(path):
    """sha256 of the file's bytes, or None when it does not exist."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def default_cache_dir(root):
    return Path(os.environ.get(CACHE_DIR_ENV) or Path(root) / ".coderstanding" / "cache")


def write_gitignore(root):
    """Keep the cache and graph.json out of the scanned repo's git."""
    folder = Path(root) / ".coderstanding"
    folder.mkdir(exist_ok=True)
    marker = folder / ".gitignore"
    if not marker.exists():
        marker.write_text(GITIGNORE_TEXT, encoding="utf-8")


class ScanCache:
    def __init__(self, root, directory):
        self.root = Path(root)
        self.directory = Path(directory)
        self.live = set()  # entry file names used or written this scan

    def _entry_path(self, rel_path, module_name):
        name = hashlib.sha1(f"{module_name}:{rel_path}".encode()).hexdigest() + ".json"
        self.live.add(name)
        return self.directory / name

    def _dependencies_unchanged(self, deps):
        return all(file_hash(self.root / rel) == digest for rel, digest in deps.items())

    def load(self, rel_path, module_name, digest):
        """(FileResult, deps) for a still-valid entry, else None."""
        try:
            entry = json.loads(self._entry_path(rel_path, module_name).read_text(encoding="utf-8"))
            if (entry["version"], entry["module"], entry["path"], entry["hash"]) != (
                    SCANNER_VERSION, module_name, rel_path, digest):
                return None
            if not self._dependencies_unchanged(entry["deps"]):
                return None
            return decode(entry["result"]), entry["deps"]
        except (OSError, ValueError, KeyError, TypeError):
            return None  # missing or damaged entry: just parse again

    def store(self, rel_path, module_name, digest, result, dep_paths):
        deps = {rel: file_hash(self.root / rel) for rel in sorted(dep_paths)}
        entry = {"version": SCANNER_VERSION, "module": module_name, "path": rel_path,
                 "hash": digest, "deps": deps, "result": encode(result)}
        self.directory.mkdir(parents=True, exist_ok=True)
        self._entry_path(rel_path, module_name).write_text(json.dumps(entry), encoding="utf-8")

    def prune(self):
        """Delete entries no file used this scan (deleted or no longer scanned files)."""
        if self.directory.is_dir():
            for path in self.directory.glob("*.json"):
                if path.name not in self.live:
                    path.unlink()
