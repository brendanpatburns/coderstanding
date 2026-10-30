"""The hide-by-line-kind filter (`show["hide_kinds"]`, CLI `--hide-kind`, server `hide_kind`)."""
from types import SimpleNamespace

from coderstanding.cli import view_options
from coderstanding.model import Edge, Graph, Node
from coderstanding.notes import Notes
from coderstanding.server import view_args
from coderstanding.textout import view_command, view_text
from coderstanding.views import compute_view


def kinded_graph():
    graph = Graph(repo="t")
    for name in "ABC":
        graph.add_node(Node(id=name, name=name, kind="class", lang="x", file=f"{name}.gd", level=1))
    graph.add_edge(Edge("A", "B", "dependency"))
    graph.add_edge(Edge("A", "B", "signal"))  # one merged line, two kinds
    graph.add_edge(Edge("B", "C", "composition"))
    return graph


def line_keys(**show):
    return {e["key"] for e in compute_view(kinded_graph(), show=show)["edges"]}


def test_a_line_goes_only_when_all_its_kinds_are_hidden():
    assert line_keys() == {"A→B", "B→C"}
    assert line_keys(hide_kinds=["signal"]) == {"A→B", "B→C"}  # partial: still drawn
    assert line_keys(hide_kinds=["signal", "dependency"]) == {"B→C"}
    assert line_keys(hide_kinds=["composition"]) == {"A→B"}


def test_hiding_lines_keeps_every_class():
    view = compute_view(kinded_graph(), show={"hide_kinds": ["signal", "dependency", "composition"]})
    assert view["edges"] == [] and {n["id"] for n in view["nodes"]} == {"A", "B", "C"}
    assert view["hidden_kinds"] == ["composition", "dependency", "signal"]


def test_text_and_command_name_the_hidden_kinds():
    show = {"hide_kinds": ["signal"], "hide_types": [".cs"]}
    lines = view_text("t", kinded_graph(), Notes(), show=show).splitlines()
    assert lines[0] == "$ coderstanding view t --level 1 --hide-type .cs --hide-kind signal"
    assert lines[1] == "hidden types: .cs" and lines[2] == "hidden kinds: signal"
    assert view_command("t", show={}) == "$ coderstanding view t --level 1"


def test_server_param_and_cli_flag_reach_the_engine():
    assert view_args({"hide_kind": ["signal", ""]})["show"]["hide_kinds"] == ["signal"]
    assert view_args({})["show"]["hide_kinds"] == []
    args = SimpleNamespace(concrete=False, no_scenes=False, no_external=False, no_comm=False, tests=False,
                           hide_type=[], hide_kind=["signal"])
    assert view_options(args)["show"]["hide_kinds"] == ["signal"]
