import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "claude-hook.py"


def run(stdin, tmp_path, registry):
    env = {"CODERSTANDING_REGISTRY": str(registry), "XDG_RUNTIME_DIR": str(tmp_path), "PATH": "/usr/bin"}
    done = subprocess.run([sys.executable, str(HOOK)], input=stdin, text=True,
                          capture_output=True, env=env)
    assert done.returncode == 0
    return json.loads(done.stdout)["hookSpecificOutput"] if done.stdout else None


def setup(tmp_path):
    repo = tmp_path / "my repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    registry = tmp_path / "apps.json"
    registry.write_text(json.dumps({"demo": {"path": str(repo)}}))
    return repo, registry


def event(tmp_path, tool, tool_input, cwd, session="s1"):
    return json.dumps({"hook_event_name": "PreToolUse", "session_id": session, "cwd": str(cwd),
                       "tool_name": tool, "tool_input": tool_input})


def test_hint_once_per_session(tmp_path):
    repo, registry = setup(tmp_path)
    first = run(event(tmp_path, "Read", {"file_path": str(repo / "src/a.py")}, repo), tmp_path, registry)
    assert first["hookEventName"] == "PreToolUse" and "`demo`" in first["additionalContext"]
    assert run(event(tmp_path, "Grep", {"pattern": "x"}, repo), tmp_path, registry) is None
    other = run(event(tmp_path, "Grep", {"pattern": "x"}, repo, session="s2"), tmp_path, registry)
    assert other


def test_outside_is_silent(tmp_path):
    repo, registry = setup(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert run(event(tmp_path, "Glob", {"pattern": "*.py", "path": str(outside)}, outside), tmp_path, registry) is None


def test_bash_search_and_other_commands(tmp_path):
    repo, registry = setup(tmp_path)
    assert run(event(tmp_path, "Bash", {"command": "ls src"}, repo), tmp_path, registry) is None
    assert run(event(tmp_path, "Bash", {"command": "rg foo src"}, repo), tmp_path, registry)


def test_worktree_counts_as_repo(tmp_path):
    repo, registry = setup(tmp_path)
    gitdir = repo / ".git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {gitdir}\n")
    assert run(event(tmp_path, "Grep", {"pattern": "x"}, wt), tmp_path, registry)


def test_session_start_note(tmp_path):
    repo, registry = setup(tmp_path)
    out = run(json.dumps({"hook_event_name": "SessionStart", "session_id": "s", "cwd": str(repo / "src")}),
              tmp_path, registry)
    assert out["hookEventName"] == "SessionStart" and "coderstanding" in out["additionalContext"]


def test_broken_json_is_silent(tmp_path):
    _, registry = setup(tmp_path)
    assert run("{not json", tmp_path, registry) is None
    assert run("", tmp_path, registry) is None
