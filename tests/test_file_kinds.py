"""File kinds: the name -> kind map, levels, the view filter, text marks and the saved graph."""
import pytest

from coderstanding.filekinds import file_kind_of, mark_file_kinds
from coderstanding.graph_json import node_from_json, node_to_json
from coderstanding.levels import guess_levels
from coderstanding.model import Edge, Graph, Node
from coderstanding.notes import Notes
from coderstanding.textout import view_command, view_text
from coderstanding.views import compute_view


@pytest.mark.parametrize("file, lang, kind", [
    ("a/Player.gd", "gdscript", "Code"), ("a/main.tscn", "godot", "Scene"), ("a/x.tres", "", "Scene"),
    ("project.godot", "godot", "Configuration"), ("a/app.cfg", "", "Configuration"),
    ("a/data.JSON", "", "Data"), ("notes.txt", "", "Data"),
    ("app/build.gradle", "", "Build"), ("app/build.gradle.kts", "kotlin", "Build"),
    ("Dockerfile", "", "Build"), ("web/package.json", "", "Build"),
    ("index.html", "html", "Web page"), ("README.md", "", "Docs"),
    ("a/odd.xyz", "somelang", "Code"), ("a/odd.xyz", "", "Data"), ("", "", ""),
])
def test_kind_comes_from_the_file_name(file, lang, kind):
    assert file_kind_of(file, lang) == kind


def graph_with_kinds():
    graph = Graph(repo="t")
    graph.add_node(Node("Main", "Main", "class", "gd", "main.gd", flags=["autoload"]))
    graph.add_node(Node("Cfg", "Cfg", "module", "x", "app/build.gradle"))
    graph.add_node(Node("Out", "Out", "external", "", ""))
    graph.add_edge(Edge("Main", "Cfg", "composition"))
    mark_file_kinds(graph)
    return graph


def test_marking_skips_nodes_without_a_file():
    kinds = {i: n.file_kind for i, n in graph_with_kinds().nodes.items()}
    assert kinds == {"Main": "Code", "Cfg": "Build", "Out": ""}


def test_non_code_starts_one_level_lower_and_human_levels_are_not_touched():
    graph = graph_with_kinds()
    graph.add_node(Node("Plain", "Plain", "class", "gd", "plain.gd"))
    graph.add_edge(Edge("Main", "Plain", "composition"))
    mark_file_kinds(graph)
    guess_levels(graph)
    assert graph.nodes["Plain"].level == 2 and graph.nodes["Cfg"].level == 3


def test_kind_is_saved_and_read_back():
    node = graph_with_kinds().nodes["Cfg"]
    assert node_from_json(node_to_json(node)).file_kind == "Build"
    assert "file_kind" not in node_to_json(graph_with_kinds().nodes["Out"])


def test_hiding_a_kind_removes_its_classes_and_the_view_counts_kinds():
    graph = graph_with_kinds()
    for node in graph.nodes.values():
        node.level = 1
    view = compute_view(graph, show={"hide_file_kinds": ["Build"]})
    assert {n["id"] for n in view["nodes"] if n["kind"] != "external"} == {"Main"}
    assert view["hidden_file_kinds"] == ["Build"] and view["file_kinds"] == {"Code": 1, "Build": 1}
    assert compute_view(graph)["file_kinds"] == {"Build": 1, "Code": 1}


def test_text_marks_only_non_code_kinds():
    graph = graph_with_kinds()
    for node in graph.nodes.values():
        node.level = 1
    text = view_text("t", graph, Notes(), show={"hide_file_kinds": ["Data"]})
    assert "--hide-file-kind Data" in text.splitlines()[0] and "hidden file kinds: Data" in text
    plain = view_text("t", graph, Notes())
    assert "Cfg [build]" in plain and "Main [" not in plain
    assert "file-kind=Build" in view_text("t", graph, Notes(), zoom={"from": "Cfg"})
    assert "file-kind" not in view_text("t", graph, Notes(), zoom={"from": "Main"})
    assert view_command("t", show={}) == "$ coderstanding view t --level 1"


def test_a_scene_keeps_the_level_code_would_get():
    """Scenes are what a Godot app is built from, so they are not pushed down like data files."""
    graph = graph_with_kinds()
    graph.add_node(Node("Hud", "hud.tscn", "scene", "gd", "scenes/hud.tscn"))
    graph.add_edge(Edge("Main", "Hud", "composition"))
    mark_file_kinds(graph)
    guess_levels(graph)
    assert graph.nodes["Hud"].file_kind == "Scene" and graph.nodes["Hud"].level == 2


def test_an_app_start_keeps_its_level_even_when_not_code():
    graph = Graph()
    graph.add_node(Node("Scene", "Scene", "scene", "godot", "main.tscn", flags=["godot-main-scene", "entry"]))
    mark_file_kinds(graph)
    guess_levels(graph)
    assert graph.nodes["Scene"].level == 1
