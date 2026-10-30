import json

import pytest

from coderstanding.graph_json import graph_from_json, graph_to_json, read_graph, write_graph
from coderstanding.model import Call, Edge, Graph, Location, Member, Node


def sample_graph():
    graph = Graph(repo="demo", scanned_at="2026-10-29T12:00:00Z")
    node = Node("GameEngine", "GameEngine", "class", "gdscript", "src/game_engine.gd", 1,
                ["autoload", "singleton"], "Node", level=1,
                members=[Member("tick", "method", "", 4)])
    loc = Location("src/game_engine.gd", 12)
    node.add_external(Edge("GameEngine", "Node3D", "dependency", ["local"], [], [loc]))
    graph.add_node(node)
    graph.add_edge(Edge("GameEngine", "Viewport", "composition", ["field", "call"],
                        [Call("render", 2, [loc], "tick")], [loc]))
    return graph


def test_json_round_trip(tmp_path):
    path = tmp_path / "out" / "graph.json"
    write_graph(sample_graph(), path)
    data = json.loads(path.read_text())
    assert data["version"] == 2 and data["stack"] == {}
    assert data["edges"][0] == {
        "from": "GameEngine", "to": "Viewport", "kind": "composition",
        "detail": ["field", "call"],
        "calls": [{"member": "render", "from_member": "tick", "count": 2,
                   "at": [{"file": "src/game_engine.gd", "line": 12}]}],
        "at": [{"file": "src/game_engine.gd", "line": 12}], "unsure": False, "auto": "",
    }
    assert data["nodes"][0]["external_refs"][0]["to"] == "Node3D"
    assert graph_to_json(read_graph(path)) == data


def test_rejects_other_versions():
    with pytest.raises(ValueError):
        graph_from_json({"version": 1, "nodes": [], "edges": []})


def test_reads_old_calls_without_from_member():
    data = graph_to_json(sample_graph())
    del data["edges"][0]["calls"][0]["from_member"]
    assert graph_from_json(data).edges[("GameEngine", "Viewport", "composition")].calls[0].from_member is None


def test_graph_from_older_scanner_loads_and_remembers_its_version():
    data = graph_to_json(sample_graph())
    data["scannerVersion"] -= 1
    assert graph_from_json(data).scanner_version == data["scannerVersion"]


def test_unknown_graph_format_is_refused():
    data = graph_to_json(sample_graph())
    data["version"] += 1
    with pytest.raises(ValueError):
        graph_from_json(data)
