"""The `nickname` note (readable names for badly named code) and the tag filter (`--tag`,
`--hide-tag`)."""

import pytest

from coderstanding.cli import main
from coderstanding.model import Edge, Graph, Node
from coderstanding.notes import Notes, check_value, node_key
from coderstanding.server import view_args
from coderstanding.textout import view_text
from coderstanding.views import compute_view, display_name
from helpers import write_files

FILES = {
    "mod.py": "class C_12:\n    def F_243e0(self):\n        return F_99()\n\n\n"
              "def F_99():\n    return 1\n\n\n"
              "class Other:\n    def run(self):\n        C_12().F_243e0()\n",
}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    main(["add", "t", str(repo)])
    batch = tmp_path / "names.txt"
    batch.write_text("# made by the project's own script\n"
                     "C_12 nickname=World\n'C_12#F_243e0' nickname=update_world\n"
                     "mod#F_99 nickname=helper_one\n")
    main(["set", "t", "--file", str(batch), "--by", "Ann"])
    return repo


def run(capsys, *argv):
    capsys.readouterr()
    main(list(argv))
    return capsys.readouterr().out.splitlines()


def test_display_name_is_the_one_text_form():
    assert display_name("F_243e0", "update_world") == "update_world (F_243e0)"
    assert display_name("F_243e0", None) == "F_243e0"


def test_a_nickname_is_one_word_that_works_as_a_target():
    check_value("nickname", "update_world")
    for bad in ("two words", "a#b", "a>b", "a,b", ""):
        with pytest.raises(ValueError):
            check_value("nickname", bad)


def test_batch_sets_nicknames_and_ai_never_overwrites_a_human_one(app, capsys):
    lines = run(capsys, "set", "t", "C_12", "nickname=Other_name")
    assert "node:mod.C_12: kept your nickname" in lines


def test_text_output_shows_nickname_then_real_name(app, capsys):
    graph = run(capsys, "graph", "t", "5")
    assert "Other > World (C_12): [dependency]" in graph
    outline = run(capsys, "outline", "t", "World")
    assert outline[0] == "t outline World (C_12)"
    assert "  World (C_12)#update_world (F_243e0)  mod.py:2-3" in outline
    node = run(capsys, "node", "t", "C_12", "--functions")
    assert node[0].startswith("t node World (C_12) ")
    assert any("update_world (F_243e0)" in line for line in node)


def test_find_searches_nicknames(app, capsys):
    lines = run(capsys, "find", "t", "helper", "one")
    assert lines[1].startswith("mod#helper_one (F_99)  function  mod.py:")


def test_commands_take_a_nickname_as_target(app, capsys):
    want = "  World (C_12)#update_world (F_243e0)  mod.py:2-3"
    assert want in run(capsys, "outline", "t", "update_world")  # a bare function nickname
    assert want in run(capsys, "outline", "t", "World#update_world")  # class and function nicknames
    assert want in run(capsys, "outline", "t", "C_12#F_243e0")  # real names still work


def test_a_missed_nickname_says_did_you_mean(app, capsys):
    with pytest.raises(SystemExit) as error:
        main(["outline", "t", "helper_ones"])
    assert "did you mean: helper_one" in str(error.value)


def test_nickname_notes_of_renamed_code_are_lost_notes(app, capsys):
    (app / "mod.py").write_text("class Other:\n    def run(self):\n        return 1\n")
    main(["rescan", "t"])
    capsys.readouterr()
    main(["graph", "t", "1"])
    assert "node:mod.C_12" in capsys.readouterr().err


# --- tag filter ---

def tagged():
    graph = Graph(repo="t")
    for name in ("A", "B", "C"):
        graph.add_node(Node(id=name, name=name, kind="class", lang="x", file=f"{name}.py", level=1))
    graph.add_edge(Edge("A", "B", "dependency"))
    notes = Notes()
    notes.set(node_key("A"), "Ann", {"tags": ["chat"]})
    notes.set(node_key("B"), "Ann", {"tags": ["audio", "chat"]})
    return graph, notes


def shown(view):
    return {n["id"] for n in view["nodes"]}


def test_hide_tag_removes_its_classes_and_keeps_its_chip():
    graph, notes = tagged()
    view = compute_view(graph, notes, show={"hide_tags": ["audio"]})
    assert shown(view) == {"A", "C"}
    assert view["tags"] == {"chat": 1, "audio": 1}
    assert view["hidden_tags"] == ["audio"]


def test_only_tags_keeps_the_classes_with_any_of_them():
    graph, notes = tagged()
    assert shown(compute_view(graph, notes, show={"tags": ["audio"]})) == {"B"}
    assert shown(compute_view(graph, notes, show={"tags": ["chat"]})) == {"A", "B"}
    assert compute_view(graph, notes)["tags"] == {"chat": 2, "audio": 1}


def test_tag_options_in_the_command_text_and_the_server():
    graph, notes = tagged()
    lines = view_text("t", graph, notes, show={"tags": ["chat"], "hide_tags": ["audio"]}).splitlines()
    assert lines[0] == "$ coderstanding view t --level 1 --tag chat --hide-tag audio"
    assert lines[1] == "hidden tags: audio"
    show = view_args({"tag": ["chat,net"], "hide_tag": ["audio", "x"]})["show"]
    assert show["tags"] == ["chat", "net"] and show["hide_tags"] == ["audio", "x"]


def test_cli_tag_flags(app, capsys):
    main(["set", "t", "Other", "tags=ui"])
    assert run(capsys, "graph", "t", "5", "--tag", "ui")[1:] == ["alone: Other"]
    assert not any("Other" in line for line in run(capsys, "graph", "t", "5", "--hide-tag", "ui"))


def test_one_function_module_shows_its_function_in_every_name_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    write_files(repo, {"solo.py": "def do_it():\n    return 1\n",
                       "user.py": "from solo import do_it\n\n\nclass User:\n    def run(self):\n        do_it()\n"})
    main(["add", "t", str(repo)])
    assert display_name("solo", "solo") == "solo"  # a title that is the name adds nothing
    assert "User > do_it (solo): [dependency] {do_it}" in run(capsys, "graph", "t", "5")
    assert "t impact do_it (solo) depth 3: 2 direct, 2 total" in run(capsys, "impact", "t", "solo")
    assert run(capsys, "tests", "t", "solo")[0].startswith("t tests do_it (solo)")
    assert any("do_it (solo)" in line for line in run(capsys, "brief", "t", "solo"))


def test_a_function_name_that_repeats_the_module_name_is_no_title(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    write_files(repo, {"414ee.py": "def FUN_000414ee():\n    return 1\n",
                       "243e0.py": "def update_world():\n    return 1\n",
                       "user.py": "from m414 import x\n"})
    main(["add", "t", str(repo)])
    graph = run(capsys, "warnings", "t", "--all") + run(capsys, "impact", "t", "243e0") + run(capsys, "impact", "t", "414ee")
    text = "\n".join(graph)
    assert "update_world (243e0)" in text
    assert "FUN_000414ee (" not in text
