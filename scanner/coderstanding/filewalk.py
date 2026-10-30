"""Find the files to scan and which language modules read them."""

import os
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from .offline import GIT_OFFLINE, GIT_OFFLINE_ENV
from .procs import run_quiet

DEFAULT_SKIP_DIRS = (".git", ".godot", "bin", "obj", "node_modules", ".venv", "addons",
                     ".coderstanding", "fixtures")


def git_files(root):
    """Repo-relative files git does not ignore, or None when root is not in a git repo."""
    try:
        out = run_quiet(
            ["git", *GIT_OFFLINE, "-C", str(root), "ls-files", "-z", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True, check=True, env={**os.environ, **GIT_OFFLINE_ENV},
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def walked_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        for name in sorted(filenames):
            yield name if rel_dir == "." else f"{rel_dir}/{name}"


def is_skipped(rel_path, skip_dirs):
    return any(part in skip_dirs for part in rel_path.split("/")[:-1])


def matches(rel_path, pattern):
    target = rel_path if "/" in pattern else rel_path.rsplit("/", 1)[-1]
    return fnmatch(target, pattern)


def list_files(root, patterns, skip_dirs=DEFAULT_SKIP_DIRS):
    """Sorted repo-relative paths under root matching any pattern, minus skipped/ignored ones."""
    root = Path(root)
    candidates = git_files(root)
    if candidates is None:
        candidates = walked_files(root)
    skip_dirs = set(skip_dirs)
    return sorted(
        rel for rel in set(candidates)
        if not is_skipped(rel, skip_dirs)
        and any(matches(rel, p) for p in patterns)
        and (root / rel).is_file()
    )


def assign_files(root, modules, skip_dirs=DEFAULT_SKIP_DIRS):
    """[(rel_path, module)] for every file, once per module whose patterns match it."""
    files = list_files(root, [p for m in modules for p in m.patterns], skip_dirs)
    return [(rel, module) for rel in files for module in modules
            if any(matches(rel, p) for p in module.patterns)]
