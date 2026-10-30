"""outline, find, impact, brief: the commands that save reading files."""

import pytest

from coderstanding.cli import main
from helpers import write_files

FILES = {
    "net.py": '"""Network layer: sends packets."""\n\n\nclass Net:\n'
              '    def join(self, room):\n        return room\n\n'
              '    def leave(self):\n        return None\n',
    "lobby.py": "from net import Net\n\n\nclass Lobby:\n    def __init__(self):\n"
                "        self.net = Net()\n\n    def start(self):\n"
                '        self.net.join("a")\n',
    "game.py": "from lobby import Lobby\n\n\nclass Game:\n    def __init__(self):\n"
               "        self.lobby = Lobby()\n\n    def run(self):\n        self.lobby.start()\n",
    "tests/test_net.py": 'from net import Net\n\n\ndef test_join():\n    Net().join("x")\n',
    "tests/test_game.py": "from game import Game\n\n\ndef test_run():\n    Game().run()\n",
}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    main(["add", "t", str(repo)])
    return repo


def run(capsys, *argv):
    capsys.readouterr()
    main(list(argv))
    return capsys.readouterr().out.splitlines()


def test_outline_has_function_ranges(app, capsys):
    lines = run(capsys, "outline", "t", "Net")
    assert lines[:2] == ["t outline Net", "[class] net.py:4-9 6 lines"]
    assert "  join  net.py:5-6" in lines and "  leave  net.py:8-9" in lines


def test_outline_of_one_function_and_a_file(app, capsys):
    assert run(capsys, "outline", "t", "Net#leave") == [
        "t outline Net#leave", "[class] net.py:4-9 6 lines", "function:", "  leave  net.py:8-9"]
    module = run(capsys, "outline", "t", "net")
    assert any(line.startswith("[module] net.py:1-") for line in module)
    assert "inside:" in module and "  Net [class]  net.py:4-9" in module


def test_outline_refuses_a_connection(app, capsys):
    with pytest.raises(SystemExit, match="not a connection"):
        run(capsys, "outline", "t", "Lobby>Net")


def test_find_ranks_name_before_function_and_limits(app, capsys):
    lines = run(capsys, "find", "t", "net")
    assert lines[0].startswith("t find net: ")
    assert lines[1].split("  ")[0] in ("Net", "net")  # a name match leads
    assert run(capsys, "find", "t", "join")[1:] == ["Net#join  method  net.py:5-6"]


def test_find_needs_every_word_and_searches_code_docs(app, capsys):
    assert run(capsys, "find", "t", "join", "nothing") == ["t find join nothing: 0/0"]
    docs = run(capsys, "find", "t", "packets")
    assert any(line.startswith("net  module") and "Network layer" in line for line in docs)


def test_find_all_and_limit(app, capsys, monkeypatch):
    monkeypatch.setattr("coderstanding.cli.FIND_LIMIT", 1)
    limited, everything = run(capsys, "find", "t", "e"), run(capsys, "find", "t", "e", "--all")
    assert len(limited) == 2 and len(everything) > 2


def test_find_hides_tests_unless_asked(app, capsys):
    assert not any("test_join" in line for line in run(capsys, "find", "t", "join"))
    assert any("test_join" in line for line in run(capsys, "find", "t", "join", "--tests"))


def test_impact_class_by_depth(app, capsys):
    lines = run(capsys, "impact", "t", "Net")
    assert lines[0] == "t impact Net depth 3: 2 direct, 4 total"
    assert lines[1].startswith("1: ") and lines[2].startswith("2: ")
    shallow = run(capsys, "impact", "t", "Net", "--depth", "1")
    assert len([l for l in shallow if l[:1].isdigit()]) == 1


def test_impact_function_follows_callers(app, capsys):
    lines = run(capsys, "impact", "t", "Net#join")
    assert "1: Lobby#start" in lines and "2: Game#__init__" not in lines
    assert lines[-1].startswith("tests: tests/test_net.py")


def test_impact_lists_tests_of_dependents(app, capsys):
    lines = run(capsys, "impact", "t", "Net")
    assert "tests/test_game.py" in lines[-1] and "tests/test_net.py" in lines[-1]
    assert run(capsys, "impact", "t", "Net", "--depth", "1")[-1].count("tests/test_game") == 0


def test_brief_is_compact_and_has_the_parts(app, capsys):
    lines = run(capsys, "brief", "t", "Net", "Lobby", "Game")
    assert lines[0] == "t brief: Net, Lobby, Game"
    assert len(lines) < 25
    assert any(l.startswith("Net  net.py:4-9  [class]") for l in lines)
    assert "fns: join 5-6, leave 8-9" in lines
    assert any(l.startswith("Lobby > Net") for l in lines)
    assert any(l.startswith("ripple: ") for l in lines)
    assert "tests: tests/test_net.py" in lines


def test_brief_of_a_function_shows_its_range(app, capsys):
    lines = run(capsys, "brief", "t", "Net#join")
    assert lines[1].startswith("Net#join  net.py:5-6")


def test_readers_store_function_end_lines(app):
    from coderstanding.scan import scan_repo
    graph = scan_repo(app, use_cache=False)
    assert {m.name: (m.line, m.end) for m in graph.nodes["net.Net"].members} == {
        "join": (5, 6), "leave": (8, 9)}
