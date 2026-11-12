"""Default domain from folder rules in settings.json: fallback under notes, shown as `rule`."""

from coderstanding.apps import find_app
from coderstanding.cli import main, missing_fields
from coderstanding.model import Graph, Node
from coderstanding.notes import Notes, node_key
from coderstanding.views import compute_view
from helpers import write_files


def make_graph():
    graph = Graph()
    for node_id, file in (("Build", "tools/build.x"), ("Main", "src/main.x")):
        graph.add_node(Node(node_id, node_id, "class", "x", file, level=1))
    return graph


def notes_with_rule():
    notes = Notes()
    notes.domain_rules = [{"files": "tools/*", "domain": "Tools"}]
    return notes


def test_rule_gives_a_fallback_domain_and_notes_win():
    graph, notes = make_graph(), notes_with_rule()
    assert notes.domain_and_author(graph.nodes["Build"]) == ("Tools", "rule")
    assert notes.domain_of(graph.nodes["Main"]) is None
    notes.set(node_key("Build"), "you", {"domain": "Dev"}, ai=False)
    assert notes.domain_and_author(graph.nodes["Build"]) == ("Dev", "you")
    assert "Tools" in notes.domains()


def test_view_rows_carry_the_rule_domain():
    graph, notes = make_graph(), notes_with_rule()
    rows = {n["id"]: n for n in compute_view(graph, notes, level=1)["nodes"]}
    assert (rows["Build"]["domain"], rows["Build"]["domain_by"]) == ("Tools", "rule")
    assert rows["Main"]["domain"] is None


def test_curate_counts_a_rule_domain_as_set():
    graph, notes = make_graph(), notes_with_rule()
    assert "domain" not in missing_fields(notes, graph.nodes["Build"])
    assert "domain" in missing_fields(notes, graph.nodes["Main"])


def test_rules_come_from_the_app_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    write_files(repo, {"a.py": "class A:\n    pass\n"})
    main(["add", "t", str(repo)])
    found = find_app("t")
    found.write_settings(domains=[{"files": "a.py", "domain": "Core"}, {"files": "", "domain": "Bad"}])
    assert found.load_notes().domain_rules == [{"files": "a.py", "domain": "Core"}]
