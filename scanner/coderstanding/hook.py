"""Claude Code hook: nudge the AI to use Coderstanding inside registered repos.

Events: SessionStart (a short note) and PreToolUse on Grep/Glob/Read/Bash (one hint per
session per app). Never blocks, never fails: any error exits 0 with no output.
Run by `coderstanding hook-run`; `coderstanding hook` prints the settings snippet that installs it.
"""
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

from . import userconfig
from .offline import install as install_offline_guard

SEARCH_COMMANDS = ("grep", "rg", "find", "cat")
DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")  # C:\code or C:/code
HINT = ("This repo is in Coderstanding (app `{app}`). Cheaper than grepping/reading blind: "
        "`coderstanding find {app} <words>`, `outline {app} <file>`, `impact {app} <class>`, "
        "`brief {app} <class>`, `graph {app} 1`, `node {app} <class>`.")
START_NOTE = ("This directory is in Coderstanding (app `{app}`). Use the `coderstanding` skill: "
              "start with `coderstanding graph {app} 1`, then `find`/`outline`/`node`/`impact` "
              "instead of broad grepping.\nOpen real files only for what the output points at.")


def git_common_dir(path):
    """The shared .git folder of the repo or worktree containing `path`, else None."""
    for folder in [path, *path.parents]:
        dot_git = folder / ".git"
        if dot_git.is_dir():
            return dot_git.resolve()
        if dot_git.is_file():
            gitdir = dot_git.read_text().strip().removeprefix("gitdir:").strip()
            gitdir = (folder / gitdir).resolve()
            common = gitdir / "commondir"
            return (gitdir / common.read_text().strip()).resolve() if common.exists() else gitdir
    return None


def inside(path, root):
    return path == root or root in path.parents


def app_for(path):
    """Name of the registered app whose repo (or worktree of it) holds `path`, else None."""
    from coderstanding.apps import load_registry
    path = path.resolve()
    registry = {name: Path(entry["path"]).resolve() for name, entry in load_registry().items()}
    for name, root in registry.items():
        if inside(path, root):
            return name
    common = git_common_dir(path)
    if common:
        for name, root in registry.items():
            if git_common_dir(root) == common:
                return name
    return None


def bash_target(command, cwd):
    """The folder a grep/rg/find/cat command works in (first path-like argument), else None."""
    words = shlex.split(command.split("|")[0].split("&&")[0], posix=not userconfig.windows())
    if not words or os.path.basename(words[0]) not in SEARCH_COMMANDS:
        return None
    for word in words[1:]:
        if word[:1] in "/~." or DRIVE_PATH.match(word):
            return cwd / Path(word).expanduser()
    return cwd


def tool_target(data):
    cwd = Path(data.get("cwd") or os.getcwd())
    args = data.get("tool_input") or {}
    tool = data.get("tool_name")
    if tool == "Read":
        return cwd / args["file_path"]
    if tool in ("Grep", "Glob"):
        return cwd / (args.get("path") or ".")
    if tool == "Bash":
        return bash_target(args.get("command", ""), cwd)
    return None


def state_file(session_id):
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(base) / f"coderstanding-hook-{re.sub(r'[^A-Za-z0-9_-]', '_', session_id)}.json"


def first_time(session_id, app):
    """True once per session and app (records it)."""
    path = state_file(session_id)
    try:
        seen = json.loads(path.read_text())
    except (OSError, ValueError):
        seen = []
    if app in seen:
        return False
    path.write_text(json.dumps(seen + [app]))
    return True


def answer(event, app):
    text = START_NOTE if event == "SessionStart" else HINT
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text.format(app=app)}}


def handle(data):
    event = data.get("hook_event_name")
    if event == "SessionStart":
        target = Path(data.get("cwd") or os.getcwd())
    elif event == "PreToolUse":
        target = tool_target(data)
    else:
        return None
    app = target and app_for(target)
    if not app or not first_time(data.get("session_id") or "nosession", app):
        return None
    return answer(event, app)


def hook_command():
    """How settings.json should call the hook: the bare command when it is on PATH, else the
    installed entry point's full path (helper.launch_command)."""
    from .helper import launch_command
    if shutil.which("coderstanding"):
        return "coderstanding hook-run"
    return " ".join(f'"{word}"' for word in [*launch_command(), "hook-run"])


def settings_snippet(command=None):
    """The `hooks` JSON for ~/.claude/settings.json (merge with existing hooks)."""
    entry = {"hooks": [{"type": "command", "command": command or hook_command()}]}
    return json.dumps({"hooks": {"SessionStart": [entry],
                                 "PreToolUse": [{"matcher": "Grep|Glob|Read|Bash", **entry}]}}, indent=2)


def main():
    try:
        install_offline_guard()
        result = handle(json.loads(sys.stdin.buffer.read().decode("utf-8")))
        if result:
            print(json.dumps(result))
    except Exception:
        pass
    return 0
