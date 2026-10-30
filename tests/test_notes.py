import json

import pytest

from coderstanding.model import Graph, Node
from coderstanding.notes import (Notes, clean_value, edge_key, function_key, load_notes, node_key,
                                parse_tags, save_notes)


def graph_with(*ids):
    graph = Graph()
    for node_id in ids:
        graph.add_node(Node(id=node_id, name=node_id, kind="class", lang="x", file="f"))
    return graph


def test_keys():
    assert node_key("VFX") == "node:VFX"
    assert edge_key("A", "B") == "edge:A→B"


def test_claude_never_overwrites_you():
    notes = Notes()
    notes.set("node:A", "you", {"hint": "mine"})
    skipped = notes.set("node:A", "claude", {"hint": "draft", "level": 3})
    assert skipped == ["hint"]
    assert notes.get("node:A", "hint") == ("mine", "you")
    assert notes.get("node:A", "level") == (3, "claude")


def test_you_replace_claude_draft():
    notes = Notes()
    notes.set("node:A", "claude", {"hint": "draft"})
    notes.set("node:A", "you", {"hint": "mine"})
    assert notes.get("node:A", "hint") == ("mine", "you")
    assert ("node:A", "claude") not in notes.entries


def test_none_clears_field():
    notes = Notes()
    notes.set("node:A", "claude", {"hint": "x"})
    notes.set("node:A", "claude", {"hint": None})
    assert notes.get("node:A", "hint") == (None, None) and not notes.entries


def test_bad_fields_rejected():
    with pytest.raises(ValueError):
        Notes().set("node:A", "claude", {"colour": "red"})
    with pytest.raises(ValueError):
        Notes().set("node:A", "claude", {"level": "3"})
    with pytest.raises(ValueError):
        Notes().set("node:A", "", {"level": 3})


def test_save_load_sorted_one_per_line(tmp_path):
    notes = Notes()
    notes.set("node:B", "claude", {"level": 2, "domain": "Net"})
    notes.set("edge:A→B", "you", {"hint": "calls", "description": "long"})
    notes.set("node:B", "you", {"bindings": ["X"], "singleton": True})
    path = tmp_path / ".coderstanding" / "notes.jsonl"
    save_notes(notes, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["key"] for l in lines] == ["edge:A→B", "node:B", "node:B"]
    assert lines[0] == '{"key": "edge:A→B", "description": "long", "hint": "calls", "by": "you"}'
    assert load_notes(path).entries == notes.entries


def test_load_missing_file_is_empty(tmp_path):
    assert load_notes(tmp_path / "none.jsonl").entries == {}


def test_lost_notes_reported_not_deleted(tmp_path):
    notes = Notes()
    notes.set("node:A", "claude", {"level": 1})
    notes.set("node:Gone", "claude", {"level": 1})
    notes.set("edge:A→Gone", "you", {"hint": "x"})
    notes.set("node:A", "claude", {"domain": "Net"})
    notes.set("node:domain:Net", "you", {"description": "networking"})
    assert notes.lost(graph_with("A")) == ["edge:A→Gone", "node:Gone"]
    assert notes.get("node:Gone", "level") == (1, "claude")


def test_review_field_merges_by_author():
    notes = Notes()
    notes.set("node:A", "claude", {"review": "split net.gd", "description": "d"}, ai=True)
    assert notes.get("node:A", "review") == ("split net.gd", "claude")
    assert notes.is_ai("node:A", "claude")
    notes.set("node:A", "you", {"review": "mine"})
    notes.set("node:A", "claude", {"review": "later AI"}, ai=True)
    assert notes.get("node:A", "review") == ("mine", "you")
    with pytest.raises(ValueError):
        notes.set("node:A", "you", {"review": 1})


def test_status_tags_rules_fields():
    notes = Notes()
    notes.set("node:A", "you", {"status": "fragile", "tags": ["net", "core"], "rules": "a\nb"})
    assert notes.get("node:A", "status") == ("fragile", "you")
    assert notes.tags() == ["core", "net"]
    for bad in ({"status": "shaky"}, {"tags": "net"}, {"tags": [""]}, {"tags": [3]}, {"rules": 1}):
        with pytest.raises(ValueError):
            notes.set("node:A", "you", bad)


def test_parse_tags_and_clean_value():
    assert parse_tags(" net, core,net ,, ") == ["net", "core"]
    assert parse_tags(["a", " a ", "b"]) == ["a", "b"]
    assert parse_tags(" , ") is None
    assert clean_value("tags", "") is None and clean_value("hint", "") is None
    assert clean_value("status", "wip") == "wip"


def test_ai_flag_saved_and_old_claude_lines_are_ai(tmp_path):
    path = tmp_path / "notes.jsonl"
    path.write_text('{"key": "node:A", "hint": "old draft", "by": "claude"}\n'
                    '{"key": "node:A", "level": 2, "by": "bot", "ai": true}\n', encoding="utf-8")
    notes = load_notes(path)
    assert notes.is_ai("node:A", "claude") and notes.is_ai("node:A", "bot")
    save_notes(notes, path)
    assert '"by": "claude", "ai": true}' in path.read_text(encoding="utf-8")


def test_any_human_beats_ai():
    notes = Notes()
    notes.set("node:A", "claude", {"hint": "draft"})
    notes.set("node:A", "ana", {"level": 2}, ai=False)
    notes.set("node:A", "bob", {"hint": "bob's"}, ai=False)
    assert notes.get("node:A", "hint") == ("bob's", "bob")
    assert notes.set("node:A", "claude", {"hint": "again", "level": 5}) == ["hint", "level"]
    notes.set("node:A", "you", {"hint": "mine"})
    assert notes.get("node:A", "hint") == ("mine", "you")  # `you` first among humans


def test_function_keys_of_existing_classes():
    from coderstanding.model import Call, Edge, Member
    graph = graph_with("A", "B")
    graph.nodes["A"].members.append(Member("run", "method"))
    graph.add_edge(Edge("A", "B", "association", calls=[Call("go", 1, from_member="<init>")]))
    notes = Notes()
    for key in ("node:A#run", "node:A#<init>", "node:A#gone", "node:Z#run", "node:B#go"):
        notes.set(key, "you", {"hint": "x"})
    assert function_key("A", "run") == "node:A#run"
    assert notes.lost(graph) == ["node:A#gone", "node:B#go", "node:Z#run"]


def test_ignore_warnings_field():
    notes = Notes()
    notes.set("node:A", "you", {"ignore_warnings": ["unused", "god"]})
    assert notes.ignored_warnings("A") == ["unused", "god"] and notes.ignored_warnings("B") == []
    for bad in ({"ignore_warnings": ["nope"]}, {"ignore_warnings": "unused"},
                {"ignore_warnings": [""]}):
        with pytest.raises(ValueError):
            notes.set("node:A", "you", bad)
    assert clean_value("ignore_warnings", []) is None
    assert clean_value("ignore_warnings", ["unused"]) == ["unused"]
