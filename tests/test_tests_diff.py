"""tests --diff: the test files to run for the git diff, through retest_files."""

import subprocess

import pytest

from coderstanding.cli import main
from coderstanding.filewalk import git_changed_lines
from helpers import write_files

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
BODY = "    def run(self):\n        x = 1\n        return x\n"
EDITED = BODY.replace("x = 1", "x = 2")


def git(repo, *args):
    subprocess.run([*GIT, "-C", str(repo), *args], check=True, capture_output=True)


CORE_FILES = {
    "core.py": f"class Core:\n{BODY}",
    "user.py": "from core import Core\n\n\nclass User:\n    def __init__(self):\n        self.core = Core()\n\n    def go(self):\n        self.core.run()\n",
    "lonely.py": f"class Lonely:\n{BODY}",
    "tests/test_core.py": "from core import Core\n\n\ndef test_core():\n    Core().run()\n",
    "tests/test_user.py": "from user import User\n\n\ndef test_user():\n    User().go()\n",
}


def make_repo(tmp_path, monkeypatch, files):
    """A committed git repo with the files, registered as app `t`."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write_files(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    main(["add", "t", str(repo)])
    return repo


@pytest.fixture
def repo(tmp_path, monkeypatch):
    return make_repo(tmp_path, monkeypatch, CORE_FILES)


def run(capsys, repo, *extra):
    capsys.readouterr()
    main(["tests", "t", "--diff", *extra, "--repo", str(repo)])
    return capsys.readouterr().out.splitlines()


def test_body_change_lists_only_the_functions_own_tests(repo, capsys):
    write_files(repo, {"core.py": f"class Core:\n{EDITED}"})
    lines = run(capsys, repo)
    assert "tests/test_core.py" in lines
    assert not any("test_user" in l for l in lines.copy() if not l.startswith("run:")), lines
    assert any(l.startswith("run: pytest tests/test_core.py") for l in lines)
    assert not any(l.startswith("no tests") for l in lines)


def test_committed_change_is_included_against_a_ref(repo, capsys):
    write_files(repo, {"core.py": f"class Core:\n{EDITED}"})
    git(repo, "commit", "-am", "edit")
    assert "tests/test_core.py" in run(capsys, repo, "HEAD~1")
    assert "tests/test_core.py" not in run(capsys, repo)  # on main the default is HEAD: nothing left


def test_untested_unit_and_new_files_listed(repo, capsys):
    write_files(repo, {"lonely.py": f"class Lonely:\n{EDITED}",
                       "tests/test_new.py": "def test_new():\n    pass\n"})
    lines = run(capsys, repo)
    assert "tests/test_new.py" in lines
    assert any(l.startswith("no tests:") and "Lonely" in l for l in lines)


def test_outside_git_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    plain = tmp_path / "plain"
    write_files(plain, {"a.py": f"class A:\n{BODY}"})
    main(["add", "p", str(plain)])
    with pytest.raises(SystemExit) as exit_:
        main(["tests", "p", "--diff", "--repo", str(plain)])
    assert "not in a git repo" in str(exit_.value)
    assert git_changed_lines(plain) is None


def test_every_dependent_test_is_listed(repo, capsys):
    from coderstanding import textout
    result = {"units": [("a", None)], "untested": [], "broad": [],
              "tests": [("tests/test_own.py", "")] + [(f"tests/test_d{n}.py", "dep") for n in range(30)]}
    lines = textout.format_diff_tests("t", "ref", result).splitlines()
    assert "tests/test_own.py" in lines and "tests/test_d29.py (via dep)" in lines
    assert not any("more" in line for line in lines)


def test_def_line_change_adds_dependents_tests(repo, capsys):
    write_files(repo, {"core.py": f"class Core:\n{BODY}".replace("run(self)", "run(self, n=1)")})
    assert "tests/test_user.py (via User)" in run(capsys, repo)


def test_module_level_change_with_many_tests_is_named_not_listed(repo, capsys):
    files = {f"tests/test_m{n}.py": "from core import Core\n\n\ndef test_x():\n    Core()\n"
             for n in range(15)}
    write_files(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "more tests")
    write_files(repo, {"core.py": f"class Core:\n    LIMIT = 2\n{BODY}"})
    lines = run(capsys, repo)
    assert not any(l.startswith("tests/test_m") for l in lines)
    assert any(l.startswith("many tests reach") and "(16)" in l for l in lines), lines


def test_support_files_in_test_folders_are_not_tests(repo, capsys):
    from coderstanding.testcode import is_runnable_test
    assert not is_runnable_test("tests/helpers.py") and not is_runnable_test("tests/conftest.py")
    assert is_runnable_test("tests/test_a.py") and is_runnable_test("tests/A.spec.ts")
