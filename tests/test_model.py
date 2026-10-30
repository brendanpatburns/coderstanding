from coderstanding.model import Call, Edge, Graph, Location, Member, Node


def edge(kind="association", detail=(), call="", line=1, unsure=False, from_member=None):
    at = [Location("a.cs", line)]
    calls = [Call(call, 1, at, from_member)] if call else []
    return Edge("A", "B", kind, list(detail), calls, at, unsure)


def test_same_pair_and_kind_merge():
    graph = Graph()
    graph.add_edge(edge(detail=["field"], call="run", line=3))
    graph.add_edge(edge(detail=["field", "call"], call="run", line=5))
    graph.add_edge(edge(call="stop", line=5))
    graph.add_edge(edge(kind="dependency"))
    assert len(graph.edges) == 2
    merged = graph.edges[("A", "B", "association")]
    assert merged.detail == ["field", "call"]
    assert [(c.member, c.count) for c in merged.calls] == [("run", 2), ("stop", 1)]
    assert merged.at == [Location("a.cs", 3), Location("a.cs", 5)]


def test_unsure_only_if_every_occurrence_unsure():
    graph = Graph()
    graph.add_edge(edge(unsure=True))
    graph.add_edge(edge(unsure=False))
    assert graph.edges[("A", "B", "association")].unsure is False


def test_partial_declarations_merge_into_one_node():
    graph = Graph()
    graph.add_node(Node("A", "A", "class", "cs", "b.cs", 9, ["partial"],
                        members=[Member("x", "field", "int")]))
    graph.add_node(Node("A", "A", "class", "cs", "a.cs", 4, ["partial", "abstract"],
                        base="Base", members=[Member("y", "method")]))
    node = graph.nodes["A"]
    assert (node.file, node.line) == ("a.cs", 4)
    assert node.flags == ["partial", "abstract"]
    assert node.base == "Base"
    assert [m.name for m in node.members] == ["x", "y"]


def test_association_folds_into_composition_of_same_pair():
    graph = Graph()
    graph.add_edge(edge(kind="composition", detail=["field"]))
    graph.add_edge(edge(kind="association", detail=["call"], call="run"))
    graph.fold_held_edges()
    assert list(graph.edges) == [("A", "B", "composition")]
    assert graph.edges[("A", "B", "composition")].detail == ["field", "call"]


def test_calls_count_per_from_member_and_member():
    graph = Graph()
    for from_member in ("a", "a", "b", None):
        graph.add_edge(edge(call="run", from_member=from_member))
    calls = graph.edges[("A", "B", "association")].calls
    assert [(c.from_member, c.member, c.count) for c in calls] == [
        ("a", "run", 2), ("b", "run", 1), (None, "run", 1)]


def test_merge_locations_is_a_sorted_union():
    from coderstanding.model import Location, merge_locations
    a = [Location("a", 1), Location("a", 5), Location("b", 2)]
    b = [Location("a", 3), Location("a", 5), Location("c", 1)]
    assert merge_locations(a, b) == sorted(set(a) | set(b))
    assert merge_locations([], b) == b and merge_locations(a, []) == a
    assert merge_locations(a, b) is not a
