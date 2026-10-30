#!/usr/bin/env python3
"""Claude Code hook: nudge the AI to use Coderstanding inside registered repos.

Events: SessionStart (a short note) and PreToolUse on Grep/Glob/Read/Bash (one hint per
session per app). Never blocks, never fails: any error exits 0 with no output.
Install: see docs/FOR_CLAUDE.md. Plain stdlib; reuses the app registry from apps.py.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scanner"))

SEARCH_COMMANDS = ("grep", "rg", "find", "cat")
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
    words = shlex.split(command.split("|")[0].split("&&")[0])
    if not words or os.path.basename(words[0]) not in SEARCH_COMMANDS:
        return None
    for word in words[1:]:
        if word[:1] in "/~.":
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
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
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


def main():
    try:
        result = handle(json.load(sys.stdin))
        if result:
            print(json.dumps(result))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
