"""Explained lines first (`show["lines"]`): the view engine's one rule for quiet lines, plus the
box-face facts the view carries (used-by count, domain notes). Old and new pages/helpers mix."""
from coderstanding.model import Edge, Graph, Member, Node
from coderstanding.notes import Notes
from coderstanding.server import view_args
from coderstanding.textout import format_graph, view_command, view_text
from coderstanding.views import compute_view


def graph_and_notes():
    graph = Graph(repo="t")
    for name, level in (("A", 1), ("B", 1), ("C", 1), ("H", 2)):
        graph.add_node(Node(id=name, name=name, kind="class", lang="x", file=f"{name}.gd", level=level,
                            members=[Member("go", "function", line=1)]))
    graph.add_edge(Edge("A", "C", "dependency"))  # no hint: quiet
    graph.add_edge(Edge("C", "B", "dependency"))  # explained by its own hint
    graph.add_edge(Edge("A", "H", "dependency"))  # A ~> B via H, explained by the H -> B hop
    graph.add_edge(Edge("H", "B", "dependency"))
    notes = Notes()
    notes.set("edge:C→B", "ann", {"hint": "C asks B"})
    notes.set("edge:H→B", "ann", {"hint": "H hands work to B"})
    notes.set("node:A", "ann", {"domain": "Core"})
    notes.set("node:domain:Core", "ann", {"hint": "The core", "description": "Everything central."})
    return graph, notes


def quiet(view):
    return {e["key"]: e["quiet"] for e in view["edges"]}


def test_explained_marks_lines_without_a_hint_on_them_or_their_hops():
    graph, notes = graph_and_notes()
    view = compute_view(graph, notes, show={"lines": "explained"})
    assert quiet(view) == {"A→C": True, "A→B": False, "C→B": False}
    assert view["lines"] == "explained"


def test_without_the_switch_every_line_is_equal():
    graph, notes = graph_and_notes()
    for show in (None, {"lines": "all"}, {"lines": "bogus"}):
        view = compute_view(graph, notes, show=show)
        assert not any(quiet(view).values()) and view["lines"] == "all"


def test_fan_out_links_follow_their_class_line():
    graph, notes = graph_and_notes()
    view = compute_view(graph, notes, show={"lines": "explained"}, expand=["C"])
    by_edge = {link["edge"]: link["quiet"] for link in view["fanout"] if link["edge"]}
    assert by_edge["A→C"] is True and by_edge["C→B"] is False


def test_text_lists_explained_lines_first():
    graph, notes = graph_and_notes()
    view = compute_view(graph, notes, show={"lines": "explained"})
    lines = format_graph("t", view).splitlines()[1:]
    assert [line.split(":")[0] for line in lines] == ["A ~> B (via 1)", "C > B", "A > C"]
    text = view_text("t", graph, notes, show={"lines": "explained"}).splitlines()
    assert text[0] == "$ coderstanding view t --level 1"  # explained is the CLI default
    assert view_command("t", show={"lines": "all"}) == "$ coderstanding view t --level 1 --lines all"


def test_the_lines_param_is_optional_both_ways():
    assert "lines" not in view_args({})["show"]  # an older page: all equal
    assert "lines" not in view_args({"lines": ["sideways"]})["show"]
    assert view_args({"lines": ["explained"]})["show"]["lines"] == "explained"


def test_nodes_carry_used_by_and_the_view_its_domains_notes():
    graph, notes = graph_and_notes()
    view = compute_view(graph, notes)
    used = {n["id"]: n["used_by"] for n in view["nodes"]}
    assert used == {"A": 0, "B": 2, "C": 1}  # on this map: B is used by C and (through H) by A
    assert view["domains"] == {"Core": {"hint": "The core", "description": "Everything central."}}
