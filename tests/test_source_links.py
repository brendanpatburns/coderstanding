"""Source links (DESIGN 12a): the remote's web page and the commit, read from git's files."""

import pytest

from coderstanding.apps import App
from coderstanding.server import Api
from coderstanding.worktrees import file_link_base, head_commit, remote_url, web_base
from helpers import scan_fake

SHA = "a" * 40


def make_repo(root, remote=None, packed=False):
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    if packed:
        (git / "packed-refs").write_text(f"# pack-refs\n{SHA} refs/heads/main\n")
    else:
        (git / "refs" / "heads" / "main").write_text(SHA + "\n")
    config = "[core]\n\tbare = false\n"
    if remote:
        config += f'[remote "origin"]\n\turl = {remote}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
    (git / "config").write_text(config)


@pytest.mark.parametrize("url,expected", [
    ("https://example.org/someone/repo.git", "https://example.org/someone/repo"),
    ("https://user:tok@example.org/someone/repo", "https://example.org/someone/repo"),
    ("http://lan:3000/someone/repo.git", "http://lan:3000/someone/repo"),
    ("git@example.net:me/repo.git", "https://example.net/me/repo"),
    ("ssh://git@example.org:2222/someone/repo.git", "https://example.org/someone/repo"),
    ("/srv/git/repo.git", None),
    ("file:///srv/git/repo.git", None),
    (None, None),
])
def test_web_base(url, expected):
    assert web_base(url) == expected


def test_commit_and_remote_from_git_files(tmp_path):
    make_repo(tmp_path, "git@example.org:someone/repo.git")
    assert head_commit(tmp_path) == SHA
    assert remote_url(tmp_path) == "git@example.org:someone/repo.git"
    assert file_link_base(tmp_path) == f"https://example.org/someone/repo/src/commit/{SHA}"


def test_packed_ref_and_github_style(tmp_path):
    make_repo(tmp_path, "https://github.com/me/repo", packed=True)
    assert file_link_base(tmp_path) == f"https://github.com/me/repo/blob/{SHA}"


def test_no_remote_means_no_link(tmp_path):
    make_repo(tmp_path)
    assert file_link_base(tmp_path) is None
    assert file_link_base(tmp_path / "nowhere") is None


def test_meta_gives_root_and_web_base(tmp_path):
    graph = scan_fake(tmp_path / "repo", {"a.fk": "type A class entry\n"})
    make_repo(tmp_path / "repo", "https://example.org/someone/repo.git")
    meta = Api(App("repo", tmp_path / "repo", tmp_path / "state"), graph=graph).meta()
    assert meta["source"] == {"root": str(tmp_path / "repo"),
                              "web": f"https://example.org/someone/repo/src/commit/{SHA}"}
