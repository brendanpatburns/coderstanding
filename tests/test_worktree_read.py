"""Reading a worktree of a registered app (its own map, the app's notes) and the `read` command."""

import subprocess

import pytest

from coderstanding.cli import main
from helpers import write_files

FILES = {
    "net.py": '"""Network layer."""\n\n\nclass Net:\n'
              '    def join(self, room):\n        return room\n\n'
              '    def leave(self):\n        return None\n',
    "lobby.py": "from net import Net\n\n\nclass Lobby:\n    def start(self):\n"
                '        Net().join("a")\n',
}
# the branch adds a function above `leave`, so leave moves down 3 lines
BRANCH_NET = FILES["net.py"].replace("    def leave", "    def ping(self):\n        return 1\n\n    def leave")


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                   check=True, capture_output=True)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("CODERSTANDING_REGISTRY", str(tmp_path / "apps.json"))
    monkeypatch.setenv("CODERSTANDING_DATA_HOME", str(tmp_path / "data"))
    repo, tree, data = tmp_path / "repo", tmp_path / "wt", tmp_path / "data" / "t"
    write_files(repo, FILES)
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "start")
    git(repo, "worktree", "add", "-q", "-b", "feat", str(tree))
    (tree / "net.py").write_text(BRANCH_NET)
    main(["add", "t", str(repo), "--data", str(data)])
    main(["set", "t", "Net#leave", "hint=Leaves the room"])
    monkeypatch.chdir(tmp_path)
    return repo, tree, data


def run(capsys, *argv):
    capsys.readouterr()
    main(list(argv))
    captured = capsys.readouterr()
    return captured.out.splitlines(), captured.err


def test_read_function_in_the_registered_checkout(setup, capsys):
    out, err = run(capsys, "read", "t", "Net#leave")
    assert out == ["t read Net#leave", "Net#leave  net.py:8-9  — Leaves the room",
                   "8      def leave(self):", "9          return None"]
    assert "worktree" not in err


def test_repo_option_reads_the_worktree_with_the_apps_notes(setup, capsys):
    repo, tree, data = setup
    graph_before = (data / "graph.json").read_bytes()
    out, err = run(capsys, "read", "t", "Net#leave", "--repo", str(tree))
    assert out[1:] == ["Net#leave  net.py:11-12  — Leaves the room",
                       "11      def leave(self):", "12          return None"]
    assert "worktree" in err and "rescanned" in err
    assert (data / "graph.json").read_bytes() == graph_before  # the registered map is untouched
    maps = [p for p in (data / "worktrees").iterdir() if p.is_dir()]
    assert len(maps) == 1 and (maps[0] / "graph.json").exists() and (maps[0] / "cache").is_dir()


def test_worktree_found_from_the_current_folder_and_rescanned_only_on_change(setup, capsys, monkeypatch):
    _, tree, _ = setup
    monkeypatch.chdir(tree)
    assert run(capsys, "outline", "t", "Net#ping")[0][-1] == "  Net#ping  net.py:8-9"
    _, err = run(capsys, "outline", "t", "Net#ping")
    assert err == ""  # the worktree note was already shown: repeat calls are silent
    (tree / "net.py").write_text("\n" + BRANCH_NET)
    out, err = run(capsys, "outline", "t", "Net#ping")
    assert out[-1] == "  Net#ping  net.py:9-10" and "rescanned" in err


def test_lost_notes_line_prints_only_when_the_lost_set_changes(setup, capsys):
    main(["set", "t", "Net#join", "hint=Joins"])
    main(["set", "t", "Net#leave", "hint=Leaves the room"])
    (setup[0] / "net.py").write_text("class Net:\n    pass\n")
    main(["rescan", "t"])
    assert "belong to renamed or removed code" in run(capsys, "outline", "t", "Net")[1]
    assert run(capsys, "outline", "t", "Net")[1] == ""
    main(["set", "t", "Net", "hint=changed"])  # a note on live code: the lost set is unchanged
    assert run(capsys, "outline", "t", "Net")[1] == ""


def test_repo_option_refuses_a_folder_outside_the_repo(setup, capsys, tmp_path):
    (tmp_path / "other").mkdir()
    with pytest.raises(SystemExit, match="not a git worktree"):
        run(capsys, "read", "t", "Net", "--repo", str(tmp_path / "other"))


def test_maps_of_removed_worktrees_are_pruned_by_rescan(setup, capsys):
    repo, tree, data = setup
    run(capsys, "outline", "t", "Net", "--repo", str(tree))
    assert any(p.is_dir() for p in (data / "worktrees").iterdir())
    git(repo, "worktree", "remove", "--force", str(tree))
    main(["rescan", "t"])
    assert not any(p.is_dir() for p in (data / "worktrees").iterdir())


def test_read_signatures_places_context_and_several_targets(setup, capsys):
    out, _ = run(capsys, "read", "t", "Net", "--signatures")
    assert out[1:] == ["Net  net.py:4-9", "5      def join(self, room):", "8      def leave(self):"]
    out, _ = run(capsys, "read", "t", "net.py:6", "lobby.py:2", "--context", "0", "Lobby#start")
    assert out[1:4] == ["Net#join  net.py:5-6", "5      def join(self, room):", "6          return room"]
    assert out[4:6] == ["lobby.py:2", "2"]  # outside any function: just the line (context 0)
    assert out[6] == "Lobby#start  lobby.py:5-6"
    out, _ = run(capsys, "read", "t", "Net#join", "--context", "1")
    assert [line.split()[0] for line in out[2:]] == ["4", "5", "6", "7"]


def test_outline_and_find_print_targets_read_takes(setup, capsys):
    out, _ = run(capsys, "outline", "t", "Net")
    target = out[out.index("functions:") + 1].split()[0]
    assert target == "Net#join"
    assert run(capsys, "read", "t", target)[0][1].startswith("Net#join  net.py:5-6")
