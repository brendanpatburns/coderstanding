"""Names index over every repo file (textindex.py) and the commands using it: uses, env,
dupes, and impact's test/doc mentions."""

import pytest

from coderstanding import textindex
from coderstanding.cache import ScanCache
from coderstanding.cli import main
from helpers import write_files

FILES = {
    "app/config.py": 'import os\n\nMAX_PLAYERS = 8\nPORT = os.environ.get("APP_PORT", "80")\n'
                     'KEY_NAME = "APP_KEY"\nSECRET = os.getenv(KEY_NAME)\n'
                     'DEBUG = os.environ["APP_DEBUG"]\nMISSING = os.environ.get("APP_MISSING")\n',
    "app/server.py": "from app.config import MAX_PLAYERS\n\n\nclass Server:\n"
                     "    def full(self, n):\n        return n >= MAX_PLAYERS\n\n"
                     "    def title(self):\n        kind = \"player\"\n"
                     '        return "Welcome to the lobby"\n',
    "app/client.py": "class Client:\n    def greet(self):\n        kind = \"player\"\n"
                     '        print("Welcome to the lobby")\n        return "audio/volume"\n',
    "tests/test_server.py": "from app.server import Server\nfrom app.config import MAX_PLAYERS\n\n\n"
                            "def test_full():\n    assert Server().full(MAX_PLAYERS)\n",
    "README.md": "# Demo\n\nSet `APP_PORT` to change the port. Server allows MAX_PLAYERS.\n"
                 "The key `audio/volume` sets loudness.\n",
    "docker-compose.yml": "services:\n  app:\n    environment:\n      - APP_PORT=8080\n"
                          "      - UNUSED_VAR=1\n",
    "Dockerfile": "FROM python:3\nENV APP_KEY=dev\n",
    ".env.example": "APP_PORT=8080\n",
    "scripts/run.sh": "#!/bin/sh\nexport APP_DEBUG=1\nROOT=$(pwd)\necho $ROOT\n"
                      "APP_EXTRA=1 python -m app\n",
    "node_modules/lib/notes.md": "MAX_PLAYERS\n",
}


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A registered app in an isolated registry and data home (never the real apps.json)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODERSTANDING_REGISTRY", str(tmp_path / "registry" / "apps.json"))
    monkeypatch.setenv("CODERSTANDING_DATA_HOME", str(tmp_path / "data"))
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    main(["add", "t", str(repo), "--data", str(tmp_path / "data" / "t")])
    return repo


def run(capsys, *argv):
    capsys.readouterr()
    main(list(argv))
    return capsys.readouterr().out.splitlines()


def index_of(tmp_path):
    return textindex.read_index(tmp_path / "data" / "t" / "graph.json")


def test_index_covers_code_and_non_code_files_by_kind(app, tmp_path):
    files = index_of(tmp_path)["files"]
    kinds = {rel: entry["kind"] for rel, entry in files.items()}
    assert kinds == {"app/config.py": "src", "app/server.py": "src", "app/client.py": "src",
                     "tests/test_server.py": "test", "README.md": "doc",
                     "docker-compose.yml": "deploy", "Dockerfile": "deploy",
                     ".env.example": "deploy", "scripts/run.sh": "script"}
    assert files["app/server.py"]["names"]["MAX_PLAYERS"] == [1, 6]


def test_file_kind_rules():
    assert textindex.file_kind("deploy/test/docker-compose.yml", False) == "deploy"
    assert textindex.file_kind("tools/test_server.sh", False) == "script"
    assert textindex.file_kind("tests/helpers.py", True) == "test"
    assert textindex.file_kind("docs/a.md", False) == "doc"
    assert textindex.file_kind(".github/workflows/ci.yml", False) == "config"


def test_rescan_reads_only_changed_files(tmp_path, monkeypatch):
    write_files(tmp_path / "r", {"a.md": "Alpha\n", "b.md": "Beta\n"})
    cache = ScanCache(tmp_path / "r", tmp_path / "c")
    textindex.build_index(tmp_path / "r", (), cache=cache)
    read = []
    original = textindex.read_file
    monkeypatch.setattr(textindex, "read_file",
                        lambda rel, *args: read.append(rel) or original(rel, *args))
    (tmp_path / "r" / "b.md").write_text("Gamma\n")
    index = textindex.build_index(tmp_path / "r", (), cache=ScanCache(tmp_path / "r", tmp_path / "c"))
    assert read == ["b.md"]
    assert "Alpha" in index["files"]["a.md"]["names"] and "Gamma" in index["files"]["b.md"]["names"]


def test_uses_groups_lines_by_kind(app, capsys):
    lines = run(capsys, "uses", "t", "MAX_PLAYERS")
    assert lines[0] == "t uses MAX_PLAYERS: 6 (src 3, tests 2, docs 1)"
    assert lines[1] == "src:" and "  app/config.py:3  MAX_PLAYERS = 8" in lines
    assert "  app/server.py:6  return n >= MAX_PLAYERS" in lines
    assert "tests:" in lines and "  tests/test_server.py:6  assert Server().full(MAX_PLAYERS)" in lines
    assert lines[-1] == "docs 1: README.md"  # docs collapse to one count line per kind
    assert "  README.md:3  Set `APP_PORT` to change the port. Server allows MAX_PLAYERS." in \
        run(capsys, "uses", "t", "MAX_PLAYERS", "--all")


def test_uses_a_settings_key_matches_its_exact_text(app, capsys):
    lines = run(capsys, "uses", "t", "audio/volume")
    assert lines == ["t uses audio/volume: 2 (src 1, docs 1)", "src:",
                     '  app/client.py:5  return "audio/volume"', "docs 1: README.md"]


def test_env_lists_reads_sets_docs_and_gaps(app, capsys):
    lines = run(capsys, "env", "t")
    by_name = {line.split()[0]: line for line in lines[1:]}
    assert set(by_name) == {"APP_DEBUG", "APP_EXTRA", "APP_KEY", "APP_MISSING", "APP_PORT",
                            "UNUSED_VAR"}  # ROOT is the script's own variable
    assert by_name["APP_PORT"] == ("APP_PORT  read app/config.py:4 | set .env.example:1, "
                                   "docker-compose.yml:4 | doc README.md:3")
    assert by_name["APP_KEY"].startswith("APP_KEY [undocumented]  read app/config.py:6 | set Dockerfile:2")
    assert by_name["APP_DEBUG"].startswith("APP_DEBUG [undocumented]  read app/config.py:7 | set scripts/run.sh:2")
    assert by_name["APP_MISSING"].startswith("APP_MISSING [not set, undocumented]")
    assert by_name["APP_EXTRA"].startswith("APP_EXTRA [not read, undocumented]  read - | set scripts/run.sh:5")
    assert by_name["UNUSED_VAR"].startswith("UNUSED_VAR [not read, undocumented]")
    assert lines[0] == "t env 6 vars (gaps: 2 not read, 1 not set, 5 undocumented)"
    assert run(capsys, "env", "t", "--prefix", "APP_P")[1:] == [by_name["APP_PORT"]]


def test_shell_inline_and_quoted_sets():
    hits = textindex.env_hits(textindex.SHELL_ENV, 'A_B="x y" C=1 \\')
    assert sorted(hits) == [("A_B", "set"), ("C", "set")]
    assert textindex.env_hits(textindex.SHELL_ENV, "ROOT=$(pwd)") == []


def test_dupes_lists_literals_in_two_files_without_common_words(app, capsys):
    lines = run(capsys, "dupes", "t")
    assert lines == ["t dupes: 1/1 literals in 2+ files",
                     '"Welcome to the lobby" 2x 2 files: app/client.py:4, app/server.py:10']


def test_impact_on_a_class_lists_tests_and_docs_naming_it(app, capsys):
    lines = run(capsys, "impact", "t", "Server")
    assert "named in tests: tests/test_server.py:1,6" in lines
    assert "named in docs: README.md:3" in lines


def test_impact_on_a_constant_starts_from_the_code_using_it(app, capsys):
    lines = run(capsys, "impact", "t", "MAX_PLAYERS")
    assert lines[0] == "t impact MAX_PLAYERS depth 3: 2 direct, 2 total"
    assert lines[1] == "1: server, Server#full"  # the import and the use; not its declaration
    assert lines[-1] == "tests: tests/test_server.py"  # names it (no resolved test edge here)
    assert "named in tests: tests/test_server.py:2,6" in lines
    assert "named in docs: README.md:3" in lines


def test_declaration_lines():
    assert textindex.declares("const MAX := 3", "MAX")
    assert textindex.declares("export const MAX: number = 3", "MAX")
    assert textindex.declares("MAX = 3", "MAX")
    assert not textindex.declares("if MAX == 3:", "MAX")
    assert not textindex.declares("total = MAX", "MAX")


def test_unknown_name_still_errors(app):
    with pytest.raises(SystemExit, match="no class named Nope"):
        main(["impact", "t", "Nope"])


def test_without_a_names_index_impact_works_and_uses_asks_for_a_rescan(app, tmp_path, capsys):
    (tmp_path / "data" / "t" / textindex.INDEX_FILE).unlink()
    assert run(capsys, "impact", "t", "Server")[0].startswith("t impact Server depth 3")
    with pytest.raises(SystemExit, match="rescan t"):
        main(["uses", "t", "Server"])


def test_find_text_hits_every_kind_with_enclosing_unit(app, capsys):
    out = run(capsys, "find", "t", "--text", "MAX_PLAYERS")
    assert out[0] == "t find --text MAX_PLAYERS: 6/6"
    assert any(l.startswith("app/server.py:6  Server#full  [src]  return n >= MAX_PLAYERS") for l in out)
    assert any(l.startswith("tests/test_server.py:6  ") and "[test]" in l for l in out)
    assert "README.md:3  Demo  [doc]  Set `APP_PORT` to change the port. Server allows MAX_PLAYERS." in out
    assert [l.split("[")[1][:3] for l in out[1:]] == ["src", "src", "src", "tes", "tes", "doc"]
    assert not any("node_modules" in l for l in out)  # the walk's skip rules


def test_find_text_regex_literal_fallback_zero_hits_and_cap(app, capsys):
    assert run(capsys, "find", "t", "--text", r"APP_(PORT|KEY)\b")[0].endswith("/6")
    assert run(capsys, "find", "t", "--text", "audio/(volume")[0] == "t find --text audio/(volume: 0 hits"
    assert run(capsys, "find", "t", "--text", "audio/volume")[0].endswith("2/2")
    (app / "big.txt").write_text("hit\n" * 50)
    (app / "app" / "many.py").write_text("hit = 1\n" * 45)
    main(["rescan", "t"])
    out = run(capsys, "find", "t", "--text", "^hit")
    assert out[0].endswith("40/95")
    assert out[41:] == ["app/many.py: 5 hits", "big.txt: 50 hits", "(--all: every line)"]  # no file hidden
    assert all("[src]" in l for l in out[1:41])  # code first
    assert len(run(capsys, "find", "t", "--text", "^hit", "--all")) == 96


def test_find_text_path_prefix_and_both_forms_error(app, capsys):
    assert run(capsys, "find", "t", "--text", "MAX_PLAYERS", "--path", "app/")[0].endswith("3/3")
    with pytest.raises(SystemExit, match="find t --text <pattern>"):
        main(["find", "t", "x", "--text", "y"])


def test_index_skips_vendor_minified_and_wide_files(tmp_path):
    write_files(tmp_path, {"viewer/vendor/lib.js": "var needle = 1;\n", "a.min.js": "var needle;\n",
                           "wide.js": "var needle = '" + "x" * 2000 + "';\n", "ok.js": "var needle = 2;\n"})
    files = textindex.build_index(tmp_path, ["viewer/vendor/lib.js", "a.min.js", "wide.js", "ok.js"])["files"]
    assert list(files) == ["ok.js"]


def test_uses_ranks_comments_and_help_after_code(app, capsys):
    (app / "app" / "notes.py").write_text("# MAX_PLAYERS is the cap\nx = MAX_PLAYERS\nparser.add_argument(help='see `MAX_PLAYERS`')\n")
    main(["rescan", "t"])
    lines = run(capsys, "uses", "t", "MAX_PLAYERS")
    src = lines[lines.index("src:"):lines.index("tests:")]
    assert src.index("  app/notes.py:2  x = MAX_PLAYERS") < src.index("  comments / help:") < \
        src.index("  app/notes.py:1  # MAX_PLAYERS is the cap")
    assert src[-1] == "  app/notes.py:3  parser.add_argument(help='see `MAX_PLAYERS`')"


def test_uses_collapses_other_kinds_and_counts_files_past_the_cap(app, capsys):
    (app / "app" / "many.py").write_text("MAX_PLAYERS = 1\n" * 45)
    main(["rescan", "t"])
    lines = run(capsys, "uses", "t", "MAX_PLAYERS")
    assert "  app/many.py: 6 hits" in lines and "  (--all: every line)" in lines
    assert "docs 1: README.md" in lines


def test_find_without_hits_hints_at_text(app, capsys):
    assert run(capsys, "find", "t", "zzqqxx")[-1] == "for literal text: find t --text <pattern>"
