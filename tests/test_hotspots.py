"""hotspots: classes ranked by size x git churn x missing tests."""

import os
import subprocess

import pytest

from coderstanding.cli import main
from coderstanding.filewalk import git_churn
from coderstanding.languages.python import PythonModule
from coderstanding.scan import scan_repo
from coderstanding.views import hotspots
from helpers import write_files

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def commit(repo, files, message, date=None):
    write_files(repo, files)
    subprocess.run([*GIT, "-C", str(repo), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-m", message], check=True,
                   capture_output=True, env={**os.environ, **({"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else {})})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([*GIT, "-C", str(repo), "init", "-q"], check=True)
    body = "    def run(self):\n" + "        x = 1\n" * 8
    commit(repo, {"calm.py": f"class Calm:\n{body}"}, "old", date="2020-01-01T00:00:00")
    commit(repo, {"hot.py": f"class Hot:\n{body}", "tested.py": f"class Tested:\n{body}",
                  "tests/test_tested.py": "from tested import Tested\n\n\n"
                  "def test_it():\n    Tested().run()\n"}, "one")
    for n in range(2):
        commit(repo, {"hot.py": f"class Hot:\n{body}        y = {n}\n",
                      "tested.py": f"class Tested:\n{body}        y = {n}\n"}, f"edit {n}")
    main(["add", "t", str(repo)])
    return repo


def run(capsys, *argv):
    capsys.readouterr()
    main(list(argv))
    return capsys.readouterr().out.splitlines()


def test_git_churn_counts_commits_per_file(repo):
    churn = git_churn(repo, 90)
    assert churn["hot.py"] == 3 and "calm.py" not in churn and churn["tested.py"] == 3


def test_git_churn_is_none_outside_git(tmp_path):
    assert git_churn(tmp_path, 90) is None


def test_hotspots_rank_untested_churn_first(repo, capsys):
    lines = run(capsys, "hotspots", "t", "--repo", str(repo))
    assert lines[0].startswith("t hotspots 90d: 2 of 2")
    assert [line.split()[0] for line in lines[1:]] == ["Hot", "Tested"]
    assert "churn 3  tested no" in lines[1] and "tested yes" in lines[2]


def test_hotspots_top_and_days(repo, capsys):
    lines = run(capsys, "hotspots", "t", "--repo", str(repo), "--top", "1")
    assert len(lines) == 2 and lines[0].startswith("t hotspots 90d: 1 of 2")
    wide = run(capsys, "hotspots", "t", "--repo", str(repo), "--days", "4000")
    assert wide[0].startswith("t hotspots 4000d: 3 of 3")


def test_hotspots_rank_module_only_files_by_lines_outside_classes(tmp_path):
    (tmp_path / "util.py").write_text("X = 1\n" * 20)
    (tmp_path / "mixed.py").write_text("A = 1\n\n\nclass K:\n    def f(self):\n        pass\n")
    graph = scan_repo(tmp_path, modules=[PythonModule()], use_cache=False)
    rows = {row["id"]: row for row in hotspots(graph, {"util.py": 2, "mixed.py": 1})}
    assert rows["util"]["size"] == 20 and rows["util"]["score"] == 40
    assert rows["mixed"]["size"] == 3 and rows["mixed.K"]["size"] == 3
