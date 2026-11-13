"""rescan warns about an old repo-local .coderstanding/cache once the data lives elsewhere."""

import pytest

from coderstanding.cli import main
from helpers import write_files


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    write_files(repo, {"a.py": "class A:\n    pass\n"})
    main(["add", "t", str(repo), "--no-scan"])
    return repo


def rescan_warning(capsys):
    capsys.readouterr()
    main(["rescan", "t"])
    return capsys.readouterr().err


def test_warns_about_a_generated_only_cache_and_keeps_it(repo, capsys):
    (repo / ".coderstanding" / "cache").mkdir(parents=True)
    (repo / ".coderstanding" / "graph.json").write_text("{}")
    assert "old generated cache" in rescan_warning(capsys)
    assert (repo / ".coderstanding" / "cache").is_dir()


def test_no_warning_without_the_folder_or_with_own_files(repo, capsys):
    assert "old generated cache" not in rescan_warning(capsys)
    (repo / ".coderstanding" / "cache").mkdir(parents=True)
    (repo / ".coderstanding" / "notes.jsonl").write_text("")
    assert "old generated cache" not in rescan_warning(capsys)
