"""The `library` setting (outside code inside the repo) and the rule lists of `settings`."""

import json

import pytest

from coderstanding.apps import AppError, find_app
from coderstanding.checks import check_graph
from coderstanding.cli import main
from coderstanding.library import apply_library, parse_library_rule
from coderstanding.model import Edge, Graph, Node
from coderstanding.notes import Notes, node_key
from coderstanding.views import compute_view
from helpers import write_files


def make_graph():
    graph = Graph()
    for node_id, file in (("Game", "game/main.c"), ("Helper", "game/helper.c"), ("c1", "out/pseudo/c1.c"),
                          ("c2", "out/pseudo/c2.c"), ("Mine", "out/pseudo/mine.c")):
        graph.add_node(Node(node_id, node_id, "module", "c", file, level=1))
    for source in ("Game", "Helper", "Mine"):
        graph.add_edge(Edge(source, "c1", "dependency"))
    return graph


def shown_ids(graph, notes, external=True):
    return {n["id"] for n in compute_view(graph, notes, level=1, show={"external": external})["nodes"]}


def test_rules_match_by_path_name_tag_and_domain():
    graph, notes = make_graph(), Notes()
    notes.set(node_key("c2"), "you", {"tags": ["library"]}, ai=False)
    notes.set(node_key("Helper"), "you", {"domain": "Graphics Library"}, ai=False)
    rules = ["out/pseudo/c1.c", "tag:library", "domain:Graphics Library", "name:Mi*"]
    assert apply_library(graph, notes, rules)
    assert {i for i, n in graph.nodes.items() if n.kind == "external"} == {"c1", "c2", "Helper", "Mine"}
    assert not apply_library(graph, notes, rules)  # unchanged: nothing to redo


def test_library_nodes_are_hidden_without_outside_code_and_keep_their_notes():
    graph, notes = make_graph(), Notes()
    notes.set(node_key("c1"), "you", {"hint": "C runtime", "domain": "C Library"}, ai=False)
    apply_library(graph, notes, ["out/pseudo/c*.c"])
    assert shown_ids(graph, notes, external=False) == {"Game", "Helper", "Mine"}
    assert {"c1", "c2"} <= shown_ids(graph, notes, external=True)
    assert notes.domain_of(graph.nodes["c1"]) == "C Library"
    assert notes.value(node_key("c1"), "hint") == "C runtime"


def test_library_nodes_do_not_count_toward_warnings_and_marks_undo():
    graph, notes = make_graph(), Notes()
    check_graph(graph)
    apply_library(graph, notes, ["out/pseudo/c*.c"])
    check_graph(graph)
    assert graph.nodes["c1"].warnings == []
    apply_library(graph, notes, [])
    assert graph.nodes["c1"].kind == "module" and "library" not in graph.nodes["c1"].flags


def test_bad_rules_are_refused():
    for text in ("", "tag:", "domain:  "):
        with pytest.raises(ValueError):
            parse_library_rule(text)


def test_settings_add_remove_and_list_rule_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    write_files(repo, {"a.py": "class A:\n    pass\n"})
    main(["add", "t", str(repo)])
    app = find_app("t")
    app.change_settings([("domains+", "game/ships/**=Ships & AI"), ("domains+", "tools/*=Tools"),
                         ("library+", "tag:library"), ("library+", "out/pseudo/9*.c"),
                         ("loaded_by+", "languages/*.py=Registry")])
    saved = json.loads(app.settings_path.read_text())
    assert saved["domains"] == [{"files": "game/ships/**", "domain": "Ships & AI"}, {"files": "tools/*", "domain": "Tools"}]
    assert saved["library"] == ["tag:library", "out/pseudo/9*.c"]
    assert saved["loaded_by"] == [{"loaded_by": "Registry", "files": "languages/*.py"}]
    assert app.domain_rules()[0]["domain"] == "Ships & AI"
    app.change_settings([("domains-", "tools/*"), ("library-", "tag:library")])
    assert app.rule_lists()["domains"] == [{"files": "game/ships/**", "domain": "Ships & AI"}]
    assert app.rule_lists()["library"] == ["out/pseudo/9*.c"]
    app.change_settings({"library": "a/*,tag:x"})
    assert app.library_rules() == ["a/*", "tag:x"]
    app.change_settings({"domains": '[{"files": "z/*", "domain": "Z"}]'})
    assert app.rule_lists()["domains"] == [{"files": "z/*", "domain": "Z"}]


def test_settings_errors_leave_the_file_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    write_files(repo, {"a.py": "class A:\n    pass\n"})
    main(["add", "t", str(repo)])
    app = find_app("t")
    for change in (("domains+", "no equals sign"), ("domains-", "nothing/**"), ("library+", "tag:")):
        with pytest.raises(AppError):
            app.change_settings([change])
    assert "domains" not in app.saved_settings()


def test_settings_command_takes_repeated_plus_items(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    write_files(repo, {"a.py": "class A:\n    pass\n"})
    main(["add", "t", str(repo)])
    main(["settings", "t", "library+=a/*", "library+=tag:lib"])
    assert find_app("t").library_rules() == ["a/*", "tag:lib"]
    assert "library=a/*,tag:lib" in capsys.readouterr().out
