import pytest

from coderstanding.levels import guess_levels
from coderstanding.model import Edge, Graph, Node


def graph_of(nodes, edges):
    graph = Graph()
    for node_id, flags in nodes.items():
        graph.add_node(Node(node_id, node_id, "class", "fake", "f", flags=flags))
    for from_id, to, kind in edges:
        graph.add_edge(Edge(from_id, to, kind))
    return graph


def levels(graph):
    return {i: n.level for i, n in graph.nodes.items()}


def test_flagged_nodes_top_then_holding_distance_capped():
    chain = [(f"C{i}", f"C{i + 1}", "composition") for i in range(6)]
    graph = graph_of({"Auto": ["autoload"], **{f"C{i}": [] for i in range(7)}, "Loose": []},
                     [("Auto", "C0", "association"), *chain, ("Auto", "Loose", "dependency")])
    guess_levels(graph)
    got = levels(graph)
    assert got["Auto"] == 1 and got["C0"] == 2 and got["C2"] == 4
    assert got["C3"] == got["C6"] == 5
    assert got["Loose"] == 5  # dependency does not carry levels


def test_without_flags_unheld_holders_are_top():
    graph = graph_of({"Root": [], "Part": [], "Alone": []}, [("Root", "Part", "composition")])
    guess_levels(graph, max_level=3)
    assert levels(graph) == {"Root": 1, "Part": 2, "Alone": 3}


def test_self_holding_edge_is_not_a_child():
    graph = graph_of({"Root": [], "Part": []},
                     [("Root", "Part", "composition"), ("Part", "Part", "association")])
    guess_levels(graph)
    assert levels(graph) == {"Root": 1, "Part": 2}


def test_tools_sit_below_the_app_start_but_lead_when_alone():
    app = graph_of({"Main": ["program-main", "entry"], "Tool": ["script-main", "entry"],
                    "Held": [], "Held2": []},
                   [("Main", "Held", "composition"), ("Tool", "Held2", "composition")])
    guess_levels(app)
    assert levels(app) == {"Main": 1, "Tool": 2, "Held": 2, "Held2": 3}
    alone = graph_of({"Tool": ["script-main", "entry"], "Held": [], "Other": []},
                     [("Tool", "Held", "composition")])
    guess_levels(alone)
    assert levels(alone) == {"Tool": 1, "Held": 2, "Other": 5}


def test_every_app_start_mechanism_is_level_one():
    names = ("godot-main", "godot-main-scene", "autoload", "program-main")
    graph = graph_of({name: [name, "entry"] for name in names} | {"Plain": []}, [])
    guess_levels(graph)
    assert {i: n.level for i, n in graph.nodes.items() if i in names} == dict.fromkeys(names, 1)
    assert graph.nodes["Plain"].level == 5


def star_graph(spokes=5):
    """Auto (app start) holds Mid; Hub touches `spokes` leaves and Mid's child Part."""
    nodes = {"Auto": ["autoload"], "Hub": [], "Part": [], "Test": ["test"],
             **{f"S{i}": [] for i in range(spokes)}}
    edges = [("Hub", f"S{i}", "dependency") for i in range(spokes)]
    edges += [("Hub", "Part", "composition"), ("Test", "Hub", "dependency"),
              ("Hub", "Hub", "dependency"), ("Auto", "S0", "dependency")]
    return graph_of(nodes, edges)


def test_hub_threshold_promotes_and_marks():
    graph = star_graph()
    guess_levels(graph, hub_threshold=6)  # Hub has 6 distinct neighbours; test/self ignored
    assert graph.nodes["Hub"].level == 1 and graph.nodes["Part"].level == 2
    assert "hub" in graph.nodes["Hub"].flags and graph.nodes["Hub"].hub_degree == 6
    assert graph.hubs == {"threshold": 6, "ids": ["Hub"]}
    assert graph.nodes["S1"].level == 5 and "hub" not in graph.nodes["S1"].flags


def test_hub_threshold_above_degree_off_and_auto():
    for setting, hubs in ((7, []), (0, []), ("auto", [])):  # auto: at least 20
        graph = star_graph()
        guess_levels(graph, hub_threshold=setting)
        assert graph.hubs["ids"] == hubs and graph.nodes["Hub"].level == 5
        assert graph.settings == {"hub_threshold": setting}


def test_auto_threshold_is_the_god_threshold():
    graph = star_graph(spokes=25)
    guess_levels(graph)
    assert graph.hubs == {"threshold": 20, "ids": ["Hub"]}


def test_parse_hub_threshold():
    from coderstanding.levels import parse_hub_threshold
    assert [parse_hub_threshold(v) for v in ("auto", "AUTO", "off", "0", 0, "30", 5)] == \
        ["auto", "auto", 0, 0, 0, 30, 5]
    for bad in ("x", "-1"):
        with pytest.raises(ValueError):
            parse_hub_threshold(bad)
