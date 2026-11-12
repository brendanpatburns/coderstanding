"""Reading commands never answer from an old map: the main map is rescanned when files changed."""

import os

import pytest

from coderstanding import apps
from coderstanding.apps import DATA_HOME_ENV, REGISTRY_ENV, find_app, register, rescan
from coderstanding.cli import main


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "apps.json"))
    monkeypatch.setenv(DATA_HOME_ENV, str(tmp_path / "share"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("class Aa:\n    def go(self):\n        pass\n")
    register("p", repo, tmp_path / "data")
    rescan(find_app("p"))
    return repo


def run(capsys, *argv):
    capsys.readouterr()
    main(list(argv))
    return capsys.readouterr()


def test_changed_files_rescan_once_and_repeat_is_silent(project, capsys):
    assert "note:" not in run(capsys, "find", "p", "Bb").err  # current map: nothing printed
    (project / "b.py").write_text("class Bb:\n    pass\n")
    first = run(capsys, "find", "p", "Bb")
    assert "Bb" in first.out
    assert "map was older than the files: rescanned" in first.err
    assert "note:" not in run(capsys, "find", "p", "Bb").err


def test_map_that_cannot_be_rescanned_says_so(project, capsys, monkeypatch):
    (project / "b.py").write_text("class Bb:\n    pass\n")

    def read_only(*args, **kwargs):
        raise PermissionError("read-only data folder")

    monkeypatch.setattr(apps, "scan_repo", read_only)
    err = run(capsys, "find", "p", "Aa").err
    assert "map is older than the files (1 changed): rescan" in err
    assert "map is older than the files" not in run(capsys, "find", "p", "Aa").err  # shown once


def test_missing_repo_folder_is_left_alone(project, capsys):
    (project / "a.py").unlink()
    project.rmdir()
    assert "older than the files" not in run(capsys, "graph", "p").err


def test_rescan_with_one_run_override_does_not_record_a_signature(project):
    app = find_app("p")
    (project / "b.py").write_text("class Bb:\n    pass\n")
    rescan(app, {"max_level": "1"})
    assert apps.refresh_map(app) == (True, 0)  # the saved settings win: rescanned again
    assert apps.refresh_map(app) == (False, 0)
