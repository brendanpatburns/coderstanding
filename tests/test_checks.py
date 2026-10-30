import pytest

from coderstanding.checks import (GOD_MIN, check_graph, glob_regex, god_threshold, ignore_reason,
                                  parse_rule, rule_matches, split_ignored, warnings_by_kind)
from coderstanding.model import Edge, Graph, Location, Node
from coderstanding.notes import Notes
from helpers import scan_fake


def kinds(graph, node_id):
    return [w["kind"] for w in graph.nodes[node_id].warnings]


def test_size_span_and_file_lines(tmp_path):
    graph = scan_fake(tmp_path, {
        "a.fk": "type a module\ntype a.Big class\nend 9\n\n\n\n\n\n\n\n",
        "b.fk": "type B class\n\n\n"})
    assert (graph.nodes["a.Big"].lines, graph.nodes["a.Big"].span) == (8, True)
    assert (graph.nodes["a"].lines, graph.nodes["a"].span) == (10, False)
    assert (graph.nodes["B"].lines, graph.nodes["B"].span) == (3, False)


def test_unused_skips_entry_tests_and_test_only_users(tmp_path):
    graph = scan_fake(tmp_path, {
        "main.fk": "type Main class entry\nref dependency Used call\n",
        "used.fk": "type Used class\n",
        "lonely.fk": "type Lonely class\n",
        "only_tested.fk": "type OnlyTested class\n",
        "tests/test_x.fk": "type TestX class\nref dependency OnlyTested call\n"})
    unused = {i for i, n in graph.nodes.items() if "unused" in kinds(graph, i)}
    assert unused == {"Lonely", "OnlyTested"}


def hub_graph(spokes):
    graph = Graph()
    for node_id in ["Hub", *(f"S{i}" for i in range(spokes))]:
        graph.add_node(Node(node_id, node_id, "class", "x", f"{node_id}.x", flags=["entry"]))
    for i in range(spokes):
        graph.add_edge(Edge("Hub", f"S{i}", "dependency"))
    return graph


def test_god_class_threshold():
    assert god_threshold([1, 2, 2, 3, 50]) == max(GOD_MIN, 12)
    assert god_threshold([10] * 9) == 60
    graph = hub_graph(GOD_MIN)
    check_graph(graph)
    god = graph.nodes["Hub"].warnings
    assert god == [{"kind": "god", "degree": GOD_MIN, "threshold": GOD_MIN}]
    graph = hub_graph(GOD_MIN - 1)
    check_graph(graph)
    assert "god" not in kinds(graph, "Hub")


def test_cycles_skip_inheritance_and_nesting(tmp_path):
    graph = scan_fake(tmp_path, {
        "a.fk": "type A class entry\nref dependency B call\n",
        "b.fk": "type B class\nref association C field\n",
        "c.fk": "type C class\nref dependency A call\n",
        "d.fk": "type D class entry\nbase E\n", "e.fk": "type E class\nref dependency D\n",
        "m.fk": "type m module entry\ntype m.Inner class\nref dependency m\nref dependency id:m\n"})
    cycle = [w for w in graph.nodes["A"].warnings if w["kind"] == "cycle"]
    assert cycle == [{"kind": "cycle", "group": "A", "size": 3}]
    assert "cycle" in kinds(graph, "C")
    assert "cycle" not in kinds(graph, "D") and "cycle" not in kinds(graph, "m.Inner")


def test_unsure_and_unresolved_counts():
    graph = Graph()
    for node_id in ("A", "B"):
        graph.add_node(Node(node_id, node_id, "class", "python", "a.py", flags=["entry"]))
    graph.add_edge(Edge("A", "B", "dependency", unsure=True))
    node = graph.nodes["A"]
    for label in ("mystery.thing", "json", "pathlib.Path"):  # stdlib names do not count
        node.add_external(Edge("A", label, "dependency", at=[Location("a.py", 1), Location("a.py", 2)]))
    check_graph(graph)
    assert graph.nodes["A"].warnings == [{"kind": "unsure", "count": 1},
                                         {"kind": "unresolved", "count": 2}]


def test_tests_and_outside_get_no_warnings(tmp_path):
    graph = scan_fake(tmp_path, {"tests/test_a.fk": "type TestA class\n"})
    assert graph.nodes["TestA"].warnings == []


def test_warnings_by_kind_order():
    graph = Graph()
    graph.add_node(Node("A", "A", "class", "x", "a", warnings=[{"kind": "unsure", "count": 1}]))
    graph.add_node(Node("B", "B", "class", "x", "b", warnings=[{"kind": "unsure", "count": 3},
                                                              {"kind": "unused"}]))
    groups = warnings_by_kind(graph)
    assert list(groups)[:2] == ["unused", "god"]
    assert [i for i, _ in groups["unsure"]] == ["B", "A"] and groups["unused"][0][0] == "B"


def test_warnings_by_kind_leaves_out_ignored_unless_asked():
    graph = Graph()
    graph.add_node(Node("B", "B", "class", "x", "b", warnings=[{"kind": "unsure", "count": 3},
                                                              {"kind": "unused"}]))
    notes = Notes()
    notes.set("node:B", "you", {"ignore_warnings": ["unused"]})
    assert warnings_by_kind(graph, notes)["unused"] == []
    assert warnings_by_kind(graph, notes, show_ignored=True)["unused"] == \
        [("B", {"kind": "unused", "reason": "note", "ignored": True})]
    assert len(warnings_by_kind(graph, notes)["unsure"]) == 1


def test_self_edges_do_not_count_as_use_cycle_or_degree(tmp_path):
    graph = scan_fake(tmp_path, {
        "a.fk": "type A class\nref dependency A call helper\nref association A\n",
        "b.fk": "type B class\nref dependency C call x\n",
        "c.fk": "type C class\nref dependency B call y\n"})
    assert ("A", "A", "dependency") in graph.edges
    assert "unused" in kinds(graph, "A")  # its own calls are not a user
    assert "cycle" not in kinds(graph, "A") and "god" not in kinds(graph, "A")


def node_at(file, lang="gdscript", kind="class", warnings=()):
    return Node(file, file, kind, lang, file, warnings=list(warnings))


def test_rule_glob_lang_kind_and_all():
    tool = node_at("tools/a/b.gd")
    assert rule_matches(parse_rule("tools/**", ["all"]), tool)
    assert not rule_matches(parse_rule("tools/*", ["all"]), tool)  # * stays in a folder
    assert rule_matches(parse_rule("tools/*/*.gd", ["all"]), tool)
    assert rule_matches(parse_rule("**/b.gd", ["all"]), tool)
    assert not rule_matches(parse_rule("src/**", ["all"]), tool)
    assert rule_matches(parse_rule("tools/**", ["all"], lang="gdscript"), tool)
    assert not rule_matches(parse_rule("tools/**", ["all"], lang="csharp"), tool)
    assert not rule_matches(parse_rule("tools/**", ["all"], kind="scene"), tool)
    assert glob_regex("a.b").match("axb") is None


def test_parse_rule_rejects_bad_input():
    for args in (("", ["unused"]), ("x/**", []), ("x/**", ["nope"])):
        with pytest.raises(ValueError):
            parse_rule(*args)
    with pytest.raises(ValueError):
        parse_rule("x/**", ["unused"], colour="red")


def test_ignore_reason_text_and_note_wins():
    node = node_at("tools/a.gd")
    rules = [parse_rule("tools/**", ["unresolved"]), parse_rule("tools/**", ["all"])]
    assert ignore_reason("unresolved", node, [], rules) == "rule tools/** unresolved"
    assert ignore_reason("unused", node, [], rules) == "rule tools/** all"
    assert ignore_reason("unused", node, ["unused"], rules) == "note"
    assert ignore_reason("unused", node, [], rules[:1]) is None


def test_split_ignored_carries_reason():
    node = node_at("tools/a.gd", warnings=[{"kind": "unused"}, {"kind": "god", "degree": 3}])
    notes = Notes()
    notes.rules = [parse_rule("tools/**", ["unused"])]
    shown, ignored = split_ignored(node, notes)
    assert [w["kind"] for w in shown] == ["god"]
    assert ignored == [{"kind": "unused", "reason": "rule tools/** unused"}]


def cycle_graph():
    graph = Graph()
    for name, folder in (("A", "src/ui"), ("B", "src/ui"), ("C", "core")):
        graph.add_node(Node(name, name, "class", "x", f"{folder}/{name}.gd",
                            warnings=[{"kind": "cycle", "group": "A", "size": 3}]))
    return graph


def test_cycle_members_dropped_and_line_gone_below_two():
    graph, notes = cycle_graph(), Notes()
    notes.rules = [parse_rule("src/ui/A.gd", ["cycle"])]
    assert [i for i, _ in warnings_by_kind(graph, notes)["cycle"]] == ["B", "C"]
    notes.rules = [parse_rule("src/ui/**", ["cycle"])]
    assert warnings_by_kind(graph, notes)["cycle"] == []  # only C left: no cycle
    assert len(warnings_by_kind(graph, notes, show_ignored=True)["cycle"]) == 3
