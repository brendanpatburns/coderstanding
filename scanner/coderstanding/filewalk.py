"""Find the files to scan and which language modules read them."""

import os
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from .offline import GIT_OFFLINE, GIT_OFFLINE_ENV
from .procs import run_quiet

DEFAULT_SKIP_DIRS = (".git", ".godot", "bin", "obj", "node_modules", ".venv", "addons",
                     ".coderstanding", "fixtures")


def git_output(root, *args):
    """Stdout bytes of a local git command run in root, or None when git fails or root is not in
    a git repo (the one place that runs git here)."""
    try:
        return run_quiet(
            ["git", *GIT_OFFLINE, "-C", str(root), *args],
            capture_output=True, check=True, env={**os.environ, **GIT_OFFLINE_ENV},
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def git_files(root):
    """Repo-relative files git does not ignore, or None when root is not in a git repo."""
    out = git_output(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if out is None:
        return None
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def git_ignored_files(root, include):
    """Repo-relative files git ignores that the `include` pathspecs (folders or globs) name."""
    if not include:
        return []
    out = git_output(root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard",
                     "--", *include)
    return [p for p in (out or b"").decode("utf-8", "replace").split("\0") if p]


def git_churn(root, days):
    """{path relative to root: commits touching it in the last `days` days}; None without git."""
    out = git_output(root, "log", f"--since={days} days ago", "--relative", "--name-only",
                     "--no-renames", "--format=%x01")
    if out is None:
        return None
    churn = {}
    for commit in out.decode("utf-8", "replace").split("\x01"):
        for path in {line for line in commit.splitlines() if line}:
            churn[path] = churn.get(path, 0) + 1
    return churn


def git_base(root):
    """The default ref to diff against: the merge base with main, else HEAD."""
    out = git_output(root, "merge-base", "main", "HEAD")
    return out.decode().strip() if out else "HEAD"


def git_range_commits(root, file, first, last, limit):
    """[(short hash, date, subject)] newest first: commits that changed lines first..last of the
    file; [] without git or history for it."""
    out = git_output(root, "log", f"-n{limit}", "--no-patch", "--format=%h\t%as\t%s", f"-L{first},{last}:{file}")
    rows = (line.split("\t", 2) for line in (out or b"").decode("utf-8", "replace").splitlines())
    return [tuple(row) for row in rows if len(row) == 3]


def git_changed_lines(root, ref=None):
    """(changed, ref) for the working tree against ref (default `git_base`), uncommitted changes
    included; changed = {path relative to root: [(first, last)] new-side lines} (a deletion = the
    line it was cut at; a new untracked file = (1, 0), the whole file); None without git."""
    ref = ref or git_base(root)
    out = git_output(root, "diff", "-U0", "--relative", "--no-renames", "--no-color", ref)
    new = git_output(root, "ls-files", "-z", "--others", "--exclude-standard")
    if out is None or new is None:
        return None
    changed, path = {}, None
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and path:
            start, _, count = line.split(" ")[2][1:].partition(",")
            first, count = int(start), int(count) if count else 1
            changed.setdefault(path, []).append((max(first, 1), max(first + count - 1, first, 1)))
    for name in new.decode("utf-8", "replace").split("\0"):
        if name:
            changed.setdefault(name, []).append((1, 0))
    return changed, ref


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


def list_files(root, patterns, skip_dirs=DEFAULT_SKIP_DIRS, include=()):
    """Sorted repo-relative paths under root matching any pattern, minus skipped/ignored ones.
    include: folders or globs whose git-ignored files are listed too (the app's `include`)."""
    root = Path(root)
    candidates = git_files(root)
    if candidates is None:
        candidates = walked_files(root)
    else:
        candidates = candidates + git_ignored_files(root, include)
    skip_dirs = set(skip_dirs)
    return sorted(
        rel for rel in set(candidates)
        if not is_skipped(rel, skip_dirs)
        and any(matches(rel, p) for p in patterns)
        and (root / rel).is_file()
    )


def assign_files(root, modules, skip_dirs=DEFAULT_SKIP_DIRS, include=()):
    """[(rel_path, module)] for every file, once per module whose patterns match it."""
    files = list_files(root, [p for m in modules for p in m.patterns], skip_dirs, include)
    return [(rel, module) for rel in files for module in modules
            if any(matches(rel, p) for p in module.patterns)]
