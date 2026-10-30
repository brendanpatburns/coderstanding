"""The hide-by-file-type filter (`show["hide_types"]`, CLI `--hide-type`) and the view's `types`."""
from coderstanding.model import Edge, Graph, Node
from coderstanding.textout import view_command, view_text
from coderstanding.views import compute_view
from coderstanding.notes import Notes


def typed_graph():
    graph = Graph(repo="t")
    for name, file in [("A", "a.gd"), ("B", "b.GD"), ("C", "c.cs"), ("D", "d.cs"), ("E", "e.py")]:
        graph.add_node(Node(id=name, name=name, kind="class", lang="x", file=file, level=1))
    graph.add_edge(Edge("A", "C", "dependency"))
    graph.add_edge(Edge("C", "E", "dependency"))
    return graph


def test_types_count_the_drawn_classes_by_extension():
    view = compute_view(typed_graph())
    assert view["types"] == {".cs": 2, ".gd": 2, ".py": 1}
    assert view["hidden_types"] == []


def test_hiding_a_type_removes_its_classes_and_keeps_its_chip():
    view = compute_view(typed_graph(), show={"hide_types": [".cs"]})
    assert {n["id"] for n in view["nodes"]} == {"A", "B", "E"}
    assert view["types"][".cs"] == 2 and view["types"][".gd"] == 2
    assert view["hidden_types"] == [".cs"]
    assert view["edges"] == []  # lines through removed classes go with them


def test_text_names_the_hidden_types_and_the_command_repeats_the_flag():
    graph, notes = typed_graph(), Notes()
    show = {"hide_types": [".cs", ".py"]}
    text = view_text("t", graph, notes, show=show)
    lines = text.splitlines()
    assert lines[0] == "$ coderstanding view t --level 1 --hide-type .cs --hide-type .py"
    assert lines[1] == "hidden types: .cs .py"
    assert view_command("t", show={}) == "$ coderstanding view t --level 1"


def test_old_scene_switches_map_onto_hide_types():
    from types import SimpleNamespace
    from coderstanding.cli import view_options
    from coderstanding.server import view_args
    args = SimpleNamespace(concrete=False, no_scenes=True, no_external=False, no_comm=False, tests=False, hide_type=[".GD"], hide_kind=[])
    assert view_options(args)["show"]["hide_types"] == [".gd", ".tscn"]
    assert view_args({"scene": ["0"]})["show"]["hide_types"] == [".tscn"]
    assert view_args({"hide": [".tscn"], "scene": ["0"]})["show"]["hide_types"] == [".tscn"]
    assert view_args({})["show"]["hide_types"] == []
