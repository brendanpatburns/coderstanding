import pytest

from coderstanding.checks import apply_note_warnings, check_graph, ignore_reason, parse_layer, warning_text
from coderstanding.cli import main
from coderstanding.model import Call, Edge, Graph, Location, Node
from coderstanding.notes import Notes


def make_graph():
    graph = Graph()
    for node_id, folder in (("Hud", "ui"), ("Client", "net"), ("Db", "data"), ("Test", "tests")):
        graph.add_node(Node(node_id, node_id, "class", "x", f"src/{folder}/{node_id}.x",
                            flags=["test"] if folder == "tests" else []))
    graph.add_edge(Edge("Hud", "Client", "association", at=[Location("src/ui/Hud.x", 42)],
                        calls=[Call("send", "<init>", 1)]))
    graph.add_edge(Edge("Hud", "Db", "dependency", at=[Location("src/ui/Hud.x", 7)]))
    graph.add_edge(Edge("Client", "Db", "dependency", at=[Location("src/net/Client.x", 3)]))
    graph.add_edge(Edge("Test", "Client", "dependency"))
    return graph


def layers(*rules):
    return [parse_layer(*rule[:3], *rule[3:]) for rule in rules]


def bad_edges(graph, rules, notes=None):
    check_graph(graph, rules, notes)
    return sorted((n.id, w["to"]) for n in graph.nodes.values() for w in n.warnings
                  if w["kind"] == "layer")


def test_forbid_names_the_edge_and_skips_tests():
    graph = make_graph()
    assert bad_edges(graph, layers(("src/ui/**", "src/net/**", "forbid", None, "UI stays dumb"))) \
        == [("Hud", "Client")]
    warning = next(w for w in graph.nodes["Hud"].warnings if w["kind"] == "layer")
    assert (warning["edge"], warning["file"], warning["line"], warning["why"]) \
        == ("association", "src/ui/Hud.x", 42, "UI stays dumb")
    assert warning_text(warning) == "layer: Client (src/net/Client.x) via association at src/ui/Hud.x:42"


def test_only_flags_every_outside_user():
    rules = layers(("src/net/**", "src/data/**", "only"))
    assert bad_edges(make_graph(), rules) == [("Hud", "Db")]  # Client is allowed, tests skipped


def test_kind_filter():
    graph = make_graph()
    assert bad_edges(graph, layers(("src/ui/**", "**", "forbid", "dependency"))) == [("Hud", "Db")]
    assert bad_edges(graph, layers(("src/ui/**", "**", "forbid", "call"))) == [("Hud", "Client")]
    with pytest.raises(ValueError):
        parse_layer("a", "b", "forbid", "bogus")


def test_domain_and_tag_targets():
    notes = Notes()
    notes.set("node:Client", "me", {"domain": "network", "tags": ["network"]})
    notes.set("node:Hud", "me", {"domain": "view"})
    assert bad_edges(make_graph(), layers(("domain:view", "domain:network", "forbid")), notes) \
        == [("Hud", "Client")]
    assert bad_edges(make_graph(), layers(("src/data/**", "tag:network", "only")), notes) \
        == [("Hud", "Client")]  # Test is skipped, Hud is outside src/data


def test_ignore_by_note_and_rule_and_reapply():
    graph = make_graph()
    rules = layers(("src/ui/**", "src/net/**", "forbid"))
    check_graph(graph, rules)
    assert ignore_reason("layer", graph.nodes["Hud"], ["layer"], []) == "note"
    assert ignore_reason("layer", graph.nodes["Hud"], [], [{"glob": "src/ui/**", "ignore": ["layer"]}]) \
        == "rule src/ui/** layer"
    apply_note_warnings(graph, [], None)  # new settings, no rescan
    assert not any(w["kind"] == "layer" for n in graph.nodes.values() for w in n.warnings)


def test_cli_add_layer_list_warnings_remove(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "py"
    (repo / "ui").mkdir(parents=True)
    (repo / "net").mkdir()
    (repo / "net/client.py").write_text("class Client:\n    def send(self): pass\n", encoding="utf-8")
    (repo / "ui/hud.py").write_text(
        "from net.client import Client\n\nclass Hud:\n    def go(self):\n        Client().send()\n",
        encoding="utf-8")
    for folder in ("ui", "net"):
        (repo / folder / "__init__.py").write_text("", encoding="utf-8")
    main(["add", "py", str(repo)])
    run = lambda *argv: (main(list(argv)), capsys.readouterr().out.splitlines())[1]
    run("rules", "py", "add", "**", "ignore=unused")
    assert run("rules", "py", "add-layer", "ui/**", "net/**", "--forbid", "--why", "UI is dumb")[0] \
        == "py rules 2"
    assert run("rules", "py")[2] == "2. ui/** forbid net/** - UI is dumb"
    out = run("warnings", "py")
    assert "layer 2" in out[0] and any(l.startswith("layer: ") and "UI is dumb" in l for l in out)
    with pytest.raises(SystemExit):
        main(["rules", "py", "add-layer", "nothing/**", "net/**", "--forbid"])
    run("rules", "py", "remove", "2")
    assert "layer 0" in run("warnings", "py")[0]
