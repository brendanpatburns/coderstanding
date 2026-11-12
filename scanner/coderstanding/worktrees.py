"""Git worktrees of a registered app's repo: where they are and their own maps' bookkeeping.

Agents edit in other worktrees (other branches) of a registered repo; reading commands then map
that worktree into `<app data>/worktrees/<key>/` (graph.json, cache/, worktree.json), so line
numbers match the branch while notes, specs, tours and settings stay the app's (apps.worktree_app).
Stdlib only and no subprocess: git's files are read directly (the Claude hook imports this too).
"""

import hashlib
import json
import re
from pathlib import Path

MAPS_DIR = "worktrees"  # in the app's data folder
RECORD = "worktree.json"  # {"path": worktree root, "signature": files' state at the last scan}


def checkout_top(path):
    """The top folder of the git checkout (main repo or worktree) holding `path`, else None."""
    path = Path(path).resolve()
    for folder in [path, *path.parents]:
        if (folder / ".git").exists():
            return folder
    return None


def git_dir(path):
    """This checkout's own .git folder (a worktree's is under the main .git/worktrees/), else None."""
    top = checkout_top(path)
    if top is None:
        return None
    dot_git = top / ".git"
    if dot_git.is_dir():
        return dot_git.resolve()
    gitdir = dot_git.read_text().strip().removeprefix("gitdir:").strip()
    return (top / gitdir).resolve()


def git_common_dir(path):
    """The shared .git folder of the repo or worktree containing `path`, else None."""
    gitdir = git_dir(path)
    if gitdir is None:
        return None
    common = gitdir / "commondir"
    return (gitdir / common.read_text().strip()).resolve() if common.exists() else gitdir


def head_commit(path):
    """The full commit hash checked out at `path` (branch or detached), else None."""
    gitdir = git_dir(path)
    if gitdir is None or not (gitdir / "HEAD").exists():
        return None
    head = (gitdir / "HEAD").read_text().strip()
    if not head.startswith("ref:"):
        return head or None
    ref = head.removeprefix("ref:").strip()
    common = git_common_dir(path)
    for folder in (gitdir, common):
        if (folder / ref).exists():
            return (folder / ref).read_text().strip()
    packed = common / "packed-refs"
    for line in packed.read_text().splitlines() if packed.exists() else []:
        if line.endswith(" " + ref):
            return line.split()[0]
    return None


def remote_url(path):
    """The URL of the repo's `origin` remote (else its first remote), else None."""
    common = git_common_dir(path)
    config = common / "config" if common else None
    if config is None or not config.exists():
        return None
    urls, remote = {}, None
    for line in config.read_text().splitlines():
        section = re.match(r'\s*\[remote "([^"]+)"\]', line)
        if section or line.lstrip().startswith("["):
            remote = section.group(1) if section else None
        elif remote and (url := re.match(r"\s*url\s*=\s*(\S+)", line)):
            urls.setdefault(remote, url.group(1))
    return urls.get("origin") or next(iter(urls.values()), None)


def web_base(url):
    """The repo's web page for a git remote URL (http(s) or ssh, any host), else None."""
    match = (re.match(r"(https?)://(?:[^@/]+@)?([^/]+)/(.+)$", url or "")
             or re.match(r"ssh://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$", url or "")
             or re.match(r"(?:[^@/\s]+@)?([^/:\s]+):(?!//)(.+)$", url or ""))
    if not match:
        return None
    scheme, host, rest = match.groups() if len(match.groups()) == 3 else ("https", *match.groups())
    rest = re.sub(r"(\.git)?/*$", "", rest)
    return f"{scheme}://{host}/{rest}" if "/" in rest else None


def file_link_base(path):
    """Where a file link starts for the checkout at `path`: the web page of the commit's tree
    (Forgejo/Gitea `/src/commit/<sha>`, GitHub `/blob/<sha>`; add `/<file>#L<line>`), else None
    when it has no usable remote or no commit."""
    base, commit = web_base(remote_url(path)), head_commit(path)
    if not base or not commit:
        return None
    return f"{base}/{'blob' if '//github.com/' in base else 'src/commit'}/{commit}"


def overlay_root(registered_root, path):
    """Where the registered app's folder is in the other checkout (worktree) holding `path`:
    None when `path` is in the registered checkout itself, or not in a checkout of the same repo."""
    registered_root = Path(registered_root).resolve()
    top, home = checkout_top(path), checkout_top(registered_root)
    if top is None or home is None or top == home:
        return None
    if git_common_dir(top) != git_common_dir(home):
        return None
    return top / registered_root.relative_to(home)


def other_code_hint(running_scanner, cwd=None):
    """A one-line hint when the current folder is in a checkout with its own `bin/coderstanding`
    (a worktree of the tool's repo) but the code running is another copy's, else None. No re-exec:
    the other checkout may have no .venv or different branch code, so the user or agent chooses."""
    top = checkout_top(cwd or Path.cwd())
    launcher = top / "bin" / "coderstanding" if top else None
    if launcher is None or not launcher.is_file() or (top / "scanner").resolve() == Path(running_scanner).resolve():
        return None
    return (f"note: this runs {Path(running_scanner).parent}, not this checkout's code; "
            f"to try the branch here use {launcher}")


def map_key(root):
    """The folder name of a worktree's map: readable name plus a hash of the full path."""
    root = str(Path(root).resolve())
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(root).name)[:40] or "root"
    return f"{name}-{hashlib.sha1(root.encode()).hexdigest()[:8]}"


def read_record(map_dir):
    try:
        return json.loads((Path(map_dir) / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_record(map_dir, root, signature, ignore_parent=True):
    """Remember the files' signature at the last scan (a worktree's map folder, or the app's data
    folder); `ignore_parent`: also keep the worktree maps folder out of git."""
    Path(map_dir).mkdir(parents=True, exist_ok=True)
    ignore = Path(map_dir).parent / ".gitignore"  # a data folder inside a repo: keep maps out of git
    if ignore_parent and not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    (Path(map_dir) / RECORD).write_text(
        json.dumps({"path": str(root), "signature": signature}) + "\n", encoding="utf-8")


SHOWN = "shown.json"


def changed_since_shown(folder, key, value):
    """True when `value` differs from what was last shown under `key` in `folder` (the CLI's
    notes print only then, so repeat calls stay silent); remembers `value` either way."""
    path = Path(folder) / SHOWN
    try:
        shown = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        shown = {}
    if shown.get(key) == value:
        return False
    shown[key] = value
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(shown) + "\n", encoding="utf-8")
    except OSError:
        pass  # a read-only data folder: the note just prints again next time
    return True


def files_signature(root, rel_paths, extra=""):
    """A hash of the files' names, sizes and change times (plus `extra`, e.g. the scan settings):
    when it is unchanged, the worktree's map is still current."""
    digest = hashlib.sha1(extra.encode())
    for rel in sorted(rel_paths):
        try:
            stat = (Path(root) / rel).stat()
        except OSError:
            continue
        digest.update(f"{rel}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def stale_maps(maps_dir, common_dir):
    """Worktree map folders whose worktree is gone or no longer belongs to the repo whose shared
    .git folder is `common_dir` (removed worktree, merged branch)."""
    maps_dir = Path(maps_dir)
    if not maps_dir.is_dir():
        return []
    stale = []
    for folder in sorted(p for p in maps_dir.iterdir() if p.is_dir() and not p.is_symlink()):
        path = read_record(folder).get("path")
        if not path or not Path(path).is_dir() or git_common_dir(path) != common_dir:
            stale.append(folder)
    return stale
