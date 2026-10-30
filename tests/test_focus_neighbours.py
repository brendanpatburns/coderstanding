"""DESIGN 7 / 7c: a focus shows its targets, one connection of neighbours faded (zoom's rule),
and the classes a focused function really touches unfaded."""

from coderstanding.model import Call, Edge, Graph, Member, Node
from coderstanding.notes import Notes
from coderstanding.textout import format_view, short_namer
from coderstanding.views import compute_view
from test_views import make_graph, node_ids, pairs


def call_graph():
    """cli (run, other) -> scan (via run), -> apps (via other); views -> cli; cache, far apart."""
    graph = make_graph({"cli": 3, "scan": 3, "apps": 3, "views": 3, "cache": 3, "far": 3},
                       [("views", "cli", "dependency"), ("scan", "cache", "dependency"),
                        ("cache", "far", "dependency")])
    graph.nodes["cli"].members = [Member("run", "method"), Member("other", "method")]
    for to, fn in (("scan", "run"), ("apps", "other")):
        graph.add_edge(Edge("cli", to, "dependency", calls=[Call("go", 1, from_member=fn)]))
    return graph


def test_class_target_shows_neighbours_faded():
    view = compute_view(call_graph(), level=3, focus=["cli"])
    assert node_ids(view, faded=False) == {"cli"}
    assert node_ids(view, faded=True) == {"scan", "apps", "views"}  # one connection, no more
    assert pairs(view, faded=True) == {"cli→scan", "cli→apps", "views→cli"}
    assert view["focus"]["nodes"] == ["cli"]


def test_function_target_unfades_what_it_calls():
    view = compute_view(call_graph(), level=3, focus=["cli#run"])
    assert node_ids(view, faded=False) == {"cli", "scan"}
    assert node_ids(view, faded=True) == {"apps", "views"}
    assert pairs(view, faded=False) == {"cli→scan"}
    assert view["focus"]["functions"] == ["cli#run"]
    assert "cli" in view["expanded"]


def test_function_target_unfades_its_callers():
    graph = call_graph()
    graph.nodes["scan"].members = [Member("start", "method")]
    graph.edges[("cli", "scan", "dependency")].calls = [Call("start", 1, from_member="run")]
    view = compute_view(graph, level=3, focus=["scan#start"])
    assert node_ids(view, faded=False) == {"scan", "cli"}
    assert node_ids(view, faded=True) == {"cache"}


def test_hidden_target_lights_stand_in_with_faded_neighbours():
    graph = make_graph({"top": 1, "inner": 3, "next": 1, "deep": 3},
                       [("top", "inner", "composition"), ("top", "next", "dependency"),
                        ("inner", "deep", "dependency")])
    view = compute_view(graph, level=1, focus=["inner"])
    assert "top" in view["focus"]["nodes"]
    assert "next" in node_ids(view, faded=True)


def test_text_lists_faded_neighbours_compactly():
    graph = call_graph()
    view = compute_view(graph, Notes(), level=3, focus=["cli"])
    text = format_view("t", view, "$ cmd", short_name=short_namer(graph))
    assert "near: apps, scan, views" in text
    assert "out:" not in text
