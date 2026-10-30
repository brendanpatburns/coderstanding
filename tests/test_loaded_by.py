"""Hand-made "A is loaded by B" links: note field, pattern rule, view line, unused, CLI."""

import pytest

from coderstanding.checks import apply_note_warnings, check_graph, parse_loaded_rule
from coderstanding.cli import main
from coderstanding.model import Edge, Graph, Node
from coderstanding.notes import Notes, node_key
from coderstanding.scan import scan_repo
from coderstanding.languages.python import PythonModule
from coderstanding.views import compute_view
from helpers import write_files


def make_graph():
    graph = Graph()
    for node_id, file in (("Registry", "core/registry.x"), ("PlugA", "plugins/a.x"),
                          ("PlugB", "plugins/b.x"), ("Other", "core/other.x")):
        graph.add_node(Node(node_id, node_id, "class", "x", file, level=1))
    graph.add_edge(Edge("Other", "Registry", "dependency"))
    return graph


def unused(graph):
    return sorted(n.id for n in graph.nodes.values() if any(w["kind"] == "unused" for w in n.warnings))


def line_kinds(view):
    return {(e["from"], e["to"]): e["kinds"] for e in view["edges"]}


def test_note_field_round_trip(tmp_path):
    from coderstanding.notes import load_notes, save_notes
    notes = Notes()
    notes.set(node_key("PlugA"), "claude", {"loaded_by": ["Registry"], "loaded_how": "by the registry"})
    save_notes(notes, tmp_path / "notes.jsonl")
    back = load_notes(tmp_path / "notes.jsonl")
    assert back.get(node_key("PlugA"), "loaded_by") == (["Registry"], "claude")
    assert back.is_ai(node_key("PlugA"), "claude")
    with pytest.raises(ValueError):
        notes.set(node_key("PlugA"), "you", {"loaded_by": "Registry"})


def test_ai_draft_never_overwrites_a_human_link():
    notes = Notes()
    notes.set(node_key("PlugA"), "you", {"loaded_by": ["Other"]}, ai=False)
    assert notes.set(node_key("PlugA"), "claude", {"loaded_by": ["Registry"]}) == ["loaded_by"]
    assert notes.value(node_key("PlugA"), "loaded_by") == ["Other"]


def test_view_draws_a_dashed_loaded_by_line_and_unused_goes():
    graph, notes = make_graph(), Notes()
    check_graph(graph)
    assert "PlugA" in unused(graph)
    notes.set(node_key("PlugA"), "you", {"loaded_by": ["Registry"], "loaded_how": "plugin list"}, ai=False)
    apply_note_warnings(graph, [], notes)
    assert "PlugA" not in unused(graph) and "PlugB" in unused(graph)
    view = compute_view(graph, notes)
    assert line_kinds(view)[("Registry", "PlugA")] == ["loaded-by"]
    node = next(n for n in view["nodes"] if n["id"] == "PlugA")
    assert node["loaders"] == [{"from": "Registry", "how": "plugin list", "source": "note",
                                "by": "you", "ai": False, "rule": None, "name": "Registry"}]
    notes.set(node_key("PlugA"), "you", {"loaded_by": None}, ai=False)  # removed: unused is back
    apply_note_warnings(graph, [], notes)
    assert "PlugA" in unused(graph)


def test_line_folds_into_the_level_one_class():
    graph, notes = make_graph(), Notes()
    graph.nodes["PlugA"].level = 2
    notes.set(node_key("PlugA"), "you", {"loaded_by": ["Other"]}, ai=False)
    view = compute_view(graph, notes, level=1)
    assert "PlugA" not in {n["id"] for n in view["nodes"]}
    assert not any(e["to"] == "PlugA" for e in view["edges"])
    assert "Other" in {n["id"] for n in view["nodes"]}


def test_pattern_rule_links_every_matching_class():
    graph, notes = make_graph(), Notes()
    notes.loaded_rules = [parse_loaded_rule("Registry", "plugins/*.x", "auto-loaded by the registry")]
    check_graph(graph, (), notes)
    assert unused(graph) == ["Other"]
    view = compute_view(graph, notes)
    assert {("Registry", "PlugA"), ("Registry", "PlugB")} <= set(line_kinds(view))
    node = next(n for n in view["nodes"] if n["id"] == "PlugB")
    assert node["loaders"][0]["source"] == "rule" and node["loaders"][0]["how"].startswith("auto")
    with pytest.raises(ValueError):
        parse_loaded_rule("", "plugins/*.x")


def test_dunder_main_module_is_an_entry(tmp_path):
    write_files(tmp_path, {"pkg/__init__.py": "", "pkg/__main__.py": "from pkg import cli\ncli.main()\n",
                           "pkg/cli.py": "def main():\n    pass\n"})
    graph = scan_repo(tmp_path, modules=[PythonModule()])
    assert graph.nodes["pkg.__main__"].flags == ["script-main", "entry"]
    assert "unused" not in [w["kind"] for w in graph.nodes["pkg.__main__"].warnings]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    write_files(repo, {"registry.py": "class Registry:\n    pass\n",
                       "plugin.py": "class Plugin:\n    pass\n"})
    main(["add", "t", str(repo)])
    return repo


def test_cli_set_and_node_text(app, capsys):
    main(["set", "t", "Plugin", "loaded_by=Registry", "loaded_how=auto-loaded by the registry"])
    capsys.readouterr()
    main(["node", "t", "Plugin"])
    out = capsys.readouterr().out
    assert "loaded by: Registry (auto-loaded by the registry)" in out
    assert "unused" not in out


def test_rules_come_from_the_app_settings(app):
    from coderstanding.apps import find_app
    found = find_app("t")
    found.write_settings(loaded_by=[parse_loaded_rule("Registry", "plugin.py", "by name")])
    assert found.load_notes().loaded_rules == [
        {"loaded_by": "Registry", "files": "plugin.py", "how": "by name"}]
