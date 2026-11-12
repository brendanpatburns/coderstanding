"""`read` target forms (bare function, path ranges, file header, Markdown section), did-you-mean
and the hints for a missing app or targets."""

import pytest

from coderstanding.cli import main
from helpers import write_files

FILES = {
    "net.py": 'import os\n\nLIMIT = 3\n\n\nclass Net:\n    def join(self, room):\n        return room\n\n'
              '    def leave(self):\n        return None\n\n\ndef helper():\n    return 1\n',
    "lobby.py": "import net\n\n\nclass Lobby:\n    def leave(self):\n        return 2\n",
    "NOTES.md": "# Notes\n\nintro\n\n## Setup\n\nrun it\n\n```\n# not a heading\n```\n\n"
                "### Deep\n\ndeep text\n\n## Usage\n\nuse it\n",
    "style.css": "a {\n  color: red;\n}\n",
}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    main(["add", "t", str(repo)])
    return repo


def read(capsys, *argv):
    capsys.readouterr()
    main(["read", "t", *argv])
    return capsys.readouterr().out.splitlines()


def fails(capsys, *argv):
    capsys.readouterr()
    with pytest.raises(SystemExit) as error:
        main(list(argv))
    return str(error.value) if isinstance(error.value.code, str) else capsys.readouterr().err


def test_bare_function_name_reads_the_one_match(app, capsys):
    out = read(capsys, "helper")
    assert out[1].startswith("net#helper  net.py:14-15")
    assert out[2].strip().startswith("14") and "def helper" in out[2]


def test_bare_function_name_with_several_matches_lists_targets_only(app, capsys):
    message = fails(capsys, "read", "t", "leave")
    assert message.splitlines() == ["error: leave is in several places; pick one:",
                                    "  Lobby#leave", "  Net#leave"]


def test_line_range_in_any_file(app, capsys):
    assert read(capsys, "NOTES.md:3-5") == ["t read NOTES.md:3-5", "NOTES.md:3-5",
                                            "3  intro", "4", "5  ## Setup"]
    assert read(capsys, "style.css:2-9")[1] == "style.css:2-3"  # clamped to the file's end


def test_file_alone_prints_header_and_definitions(app, capsys):
    out = read(capsys, "net.py")
    assert out[1] == "net.py:1-3"
    assert out[2:] == ["1  import os", "2", "3  LIMIT = 3",
                       "defs: Net, net#helper"]


def test_markdown_section_runs_to_next_heading_of_same_level(app, capsys):
    out = read(capsys, "NOTES.md#setup")
    assert out[1] == "NOTES.md:5-15"
    assert any("not a heading" in line for line in out) and not any("use it" in l for l in out)
    assert read(capsys, "NOTES.md#Usage")[1] == "NOTES.md:17-19"


def test_markdown_file_alone_lists_headings(app, capsys):
    out = read(capsys, "NOTES.md")
    assert out[2:] == ["NOTES.md#Notes", "NOTES.md#Setup", "NOTES.md#Deep", "NOTES.md#Usage"]


def test_other_file_alone_is_refused_with_the_range_form(app, capsys):
    assert "read style.css:1-3" in fails(capsys, "read", "t", "style.css")


def test_misses_say_did_you_mean(app, capsys):
    assert "did you mean: net#helper" in fails(capsys, "read", "t", "helpr")
    assert "did you mean: Net#leave" in fails(capsys, "read", "t", "Net#leav")
    assert "did you mean: NOTES.md#Setup" in fails(capsys, "read", "t", "NOTES.md#Setp")
    assert "did you mean: net.py" in fails(capsys, "read", "t", "nett.py:1-2")


def test_missing_app_or_targets_say_what_to_type(app, capsys):
    assert "type: coderstanding read <app> <target>..." in fails(capsys, "read")
    assert "registered apps: t" in fails(capsys, "read")
    assert "type: coderstanding brief <app> <targets>..." in fails(capsys, "brief", "t")
    assert "registered apps: t" in fails(capsys, "outline", "nope", "X")


@pytest.mark.parametrize("command", [["outline", "t"], ["brief", "t"], ["impact", "t"], ["node", "t"]])
def test_every_target_command_takes_a_bare_function_name(app, capsys, command):
    capsys.readouterr()
    main([*command, "helper"])
    assert "net" in capsys.readouterr().out


def test_bare_function_name_in_several_places_lists_targets_for_any_command(app, capsys):
    message = fails(capsys, "outline", "t", "leave")
    assert message.splitlines() == ["error: leave is in several places; pick one:",
                                    "  Lobby#leave", "  Net#leave"]


def test_other_commands_say_did_you_mean_too(app, capsys):
    assert "did you mean: net#helper" in fails(capsys, "outline", "t", "helpr")
    assert "did you mean: Net#leave" in fails(capsys, "brief", "t", "Net#leav")
    assert "did you mean: net#helper" in fails(capsys, "zoom", "t", "helpr", "Net")
    assert "did you mean: net.py" in fails(capsys, "setup", "t", "nett.py")
    assert "ambiguous" not in fails(capsys, "node", "t", "Nope")


def test_module_stem_with_a_line_range_reads_that_file(app, capsys):
    assert read(capsys, "net:7-8")[1:] == ["net.py:7-8", "7      def join(self, room):",
                                           "8          return room"]


def test_module_hash_class_is_the_class(app, capsys):
    out = read(capsys, "net#Net", "--signatures")
    assert out[1].startswith("Net  net.py:6")
    assert [line for line in out if "def join" in line]


def test_ambiguous_module_stem_lists_the_files(app, capsys):
    write_files(app, {"sub/__init__.py": "", "sub/net.py": "def other():\n    return 1\n"})
    main(["rescan", "t"])
    message = fails(capsys, "read", "t", "net:1-2")
    assert message.splitlines() == ["error: net is in several files; pick one:",
                                    "  net.py:1-2", "  sub/net.py:1-2"]
    assert fails(capsys, "read", "t", "nosuch:1-2").startswith("error: no file nosuch")
