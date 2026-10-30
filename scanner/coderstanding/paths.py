"""Repo-relative path helpers shared by the file walk, modules and resolver.

All paths inside the scanner are repo-relative POSIX strings ("src/a.gd").
"""

import posixpath
from functools import lru_cache
from pathlib import Path

RES_PREFIX = "res://"


@lru_cache(maxsize=None)
def godot_root(root, rel_dir):
    """Repo-relative dir of the nearest project.godot at or above rel_dir, or None."""
    while True:
        if (Path(root) / rel_dir / "project.godot").is_file():
            return rel_dir
        if rel_dir in ("", "."):
            return None
        rel_dir = posixpath.dirname(rel_dir)


def res_to_rel(res_path, godot_dir):
    return posixpath.normpath(posixpath.join(godot_dir or "", res_path[len(RES_PREFIX):]))


def rel_to_res(rel_path, godot_dir):
    return RES_PREFIX + posixpath.relpath(rel_path, godot_dir or ".")


def normalize_ref_path(path, from_file, root):
    """Turn a path as written in from_file (res://, ./, ../ or repo-relative) repo-relative."""
    if path.startswith(RES_PREFIX):
        return res_to_rel(path, godot_root(str(root), posixpath.dirname(from_file)))
    if path.startswith(("./", "../")):
        return posixpath.normpath(posixpath.join(posixpath.dirname(from_file), path))
    return posixpath.normpath(path)
