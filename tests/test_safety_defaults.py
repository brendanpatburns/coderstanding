"""Safety: tests never touch the real apps.json; `add`/`rescan` keep data out of the scanned repo;
a worktree's notes go to its own in-repo data folder; the worktree-code hint."""

import subprocess

from coderstanding import apps, worktrees
from coderstanding import cli
from coderstanding.cli import main
from conftest import REAL_REGISTRY
from helpers import write_files

FILES = {"net.py": "class Net:\n    def join(self):\n        return 1\n"}


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                   check=True, capture_output=True)


def test_tests_use_a_temp_registry():
    assert apps.registry_path() != REAL_REGISTRY
    assert "default-registry" in str(apps.registry_path())


def test_add_defaults_data_out_of_the_repo(tmp_path):
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    main(["add", "proj", str(repo)])
    assert apps.find_app("proj").data == tmp_path / "default-data" / "proj"
    assert not (repo / ".coderstanding").exists()
    assert (tmp_path / "default-data" / "proj" / "graph.json").exists()


def test_add_keeps_an_in_repo_folder_that_has_own_files(tmp_path):
    repo = tmp_path / "repo"
    write_files(repo, {**FILES, ".coderstanding/tours/a.md": "x"})
    main(["add", "proj", str(repo), "--no-scan"])
    assert apps.find_app("proj").data == repo / ".coderstanding"


def test_add_explicit_data_wins(tmp_path):
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    main(["add", "proj", str(repo), "--data", str(tmp_path / "mine"), "--no-scan"])
    assert apps.find_app("proj").data == tmp_path / "mine"


def test_rescan_of_a_plain_folder_stays_out_of_the_repo(tmp_path):
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    main(["rescan", str(repo)])
    assert not (repo / ".coderstanding").exists()
    assert (tmp_path / "default-data" / "repo" / "graph.json").exists()


def make_worktree(tmp_path):
    repo, tree = tmp_path / "repo", tmp_path / "wt"
    write_files(repo, {**FILES, ".coderstanding/tours/a.md": "x"})
    git(repo, "init", "-q")
    git(repo, "add", "."), git(repo, "commit", "-qm", "start")
    git(repo, "worktree", "add", "-q", "-b", "feat", str(tree))
    main(["add", "t", str(repo)])
    return repo, tree


def test_notes_from_a_worktree_land_in_the_worktree(tmp_path, capsys):
    repo, tree = make_worktree(tmp_path)
    main(["set", "t", "Net#join", "hint=Joins", "--repo", str(tree)])
    assert "Joins" in (tree / ".coderstanding" / "notes.jsonl").read_text()
    assert not (repo / ".coderstanding" / "notes.jsonl").exists()


def test_notes_in_main_are_seeded_into_the_worktree(tmp_path, capsys):
    repo, tree = make_worktree(tmp_path)
    main(["set", "t", "Net#join", "hint=Joins"])
    main(["set", "t", "Net", "hint=Net class", "--repo", str(tree)])
    text = (tree / ".coderstanding" / "notes.jsonl").read_text()
    assert "Joins" in text and "Net class" in text
    assert "Net class" not in (repo / ".coderstanding" / "notes.jsonl").read_text()


def test_rescan_repo_scans_the_worktree(tmp_path, capsys):
    repo, tree = make_worktree(tmp_path)
    (tree / "net.py").write_text(FILES["net.py"] + "\nclass Extra:\n    pass\n")
    main(["rescan", "t", "--repo", str(tree)])
    assert "Extra" not in (tmp_path / "default-data" / "t" / "graph.json").read_text() \
        if (tmp_path / "default-data" / "t" / "graph.json").exists() else True
    assert "Extra" in next((repo / ".coderstanding" / "worktrees").glob("*/graph.json")).read_text()


def test_hint_when_running_other_checkouts_code(tmp_path):
    tree = tmp_path / "wt"
    write_files(tree, {"bin/coderstanding": "#!/bin/sh\n", "scanner/x.py": ""})
    (tree / ".git").mkdir()
    assert "bin/coderstanding" in worktrees.other_code_hint(tmp_path / "main" / "scanner", tree)
    assert worktrees.other_code_hint(tree / "scanner", tree) is None
    assert worktrees.other_code_hint(tree / "scanner", tmp_path) is None


def test_other_code_hint_prints_once_per_checkout(tmp_path, monkeypatch, capsys):
    tree = tmp_path / "wt"
    write_files(tree, {"bin/coderstanding": "#!/bin/sh\n", "scanner/x.py": ""})
    (tree / ".git").mkdir()
    monkeypatch.chdir(tree)
    for expected in (True, False):
        cli.print_other_code_hint()
        assert ("bin/coderstanding" in capsys.readouterr().err) is expected
