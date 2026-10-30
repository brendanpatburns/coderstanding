from helpers import scan_fake


def edges(graph):
    return {(e.from_id, e.to, e.kind): e for e in graph.edges.values()}


def externals(graph, node_id):
    return sorted(to for (_, to, _) in graph.nodes[node_id].external_refs)


def test_scope_chain_then_imports_then_global(tmp_path):
    graph = scan_fake(tmp_path, {
        "a.fk": "type A.B.Outer class\nscope A.B.Outer\n"
                "ref association Inner\nref association Near\nref dependency Far\n"
                "using X\nref dependency Lib\nref dependency Top\n",
        "b.fk": "type A.B.Outer.Inner class\ntype A.B.Near class\ntype Far class\n"
                "type X.Lib class\ntype Top class\n",
    })
    got = edges(graph)
    for to in ("A.B.Outer.Inner", "A.B.Near", "Far", "X.Lib", "Top"):
        assert any(k[1] == to for k in got), to
    assert not any(e.unsure for e in got.values())


def test_two_matching_imports_is_unsure(tmp_path):
    graph = scan_fake(tmp_path, {
        "a.fk": "type A class\nusing X\nusing Y\nref dependency Foo\n",
        "b.fk": "type X.Foo class\ntype Y.Foo class\n",
    })
    assert edges(graph)[("A", "X.Foo", "dependency")].unsure is True


def test_local_alias_to_res_path(tmp_path):
    graph = scan_fake(tmp_path, {
        "game/project.godot": "",
        "game/src/main.fk": "type res://src/main.fk script\n"
                            "alias Acoustics path:res://src/audio/acoustics.fk\n"
                            "ref association Acoustics field\n"
                            "ref dependency Acoustics.Inner create\n",
        "game/src/audio/acoustics.fk": "type res://src/audio/acoustics.fk script\n"
                                       "type res://src/audio/acoustics.fk.Inner inner\n",
    })
    got = edges(graph)
    assert ("res://src/main.fk", "res://src/audio/acoustics.fk", "association") in got
    assert ("res://src/main.fk", "res://src/audio/acoustics.fk.Inner", "dependency") in got


def test_global_alias_and_marks(tmp_path):
    graph = scan_fake(tmp_path, {
        "project.fk": "type Project module\nglobal Engine path:./src/engine.fk\n"
                      "mark path:src/engine.fk autoload,singleton\n",
        "src/engine.fk": "type Engine2 class\n",
        "src/user.fk": "type User class\nref dependency Engine call tick\n",
    })
    edge = edges(graph)[("User", "Engine2", "dependency")]
    assert [(c.member, c.count) for c in edge.calls] == [("tick", 1)]
    assert graph.nodes["Engine2"].flags == ["autoload", "singleton"]


def test_relative_path_with_suffix_and_member(tmp_path):
    graph = scan_fake(tmp_path, {
        "src/a.fk": "type src/a.fk module\nref dependency path:./b#Foo import\n"
                    "ref dependency path:../lib/c#helper import\n",
        "src/b.fk": "type src/b.fk module\ntype src/b.fk.Foo class\n",
        "lib/c.fk": "type lib/c.fk module\n",
    })
    got = edges(graph)
    assert ("src/a.fk", "src/b.fk.Foo", "dependency") in got
    assert ("src/a.fk", "lib/c.fk", "dependency") in got


def test_outside_names_become_external_refs(tmp_path):
    graph = scan_fake(tmp_path, {
        "a.fk": "type A class\nbase Node3D\nref dependency Godot.Vector3 local\n"
                "ref dependency path:res://addons/x/y.fk load\n",
    })
    assert graph.edges == {}
    assert externals(graph, "A") == ["Godot.Vector3", "Node3D", "addons/x/y.fk"]
    assert graph.nodes["A"].base == "Node3D"


def test_base_on_interface_is_realization_and_self_inheritance_drops(tmp_path):
    graph = scan_fake(tmp_path, {
        "a.fk": "type A class\nbase IThing\nbase Base\nbase A\n",
        "b.fk": "type IThing interface\ntype Base class\n",
    })
    assert set(edges(graph)) == {("A", "IThing", "realization"), ("A", "Base", "generalization")}


def test_dotted_name_falls_back_to_longest_existing_prefix(tmp_path):
    graph = scan_fake(tmp_path, {
        "a.fk": "type pkg.a module\nref dependency pkg.b.helper import helper\n",
        "b.fk": "type pkg.b module\n",
    })
    assert ("pkg.a", "pkg.b", "dependency") in edges(graph)


def test_loose_names_only_for_loose_modules(tmp_path, monkeypatch):
    files = {"a.fk": "type A class\nref dependency Foo\n", "b.fk": "type N.Foo class\n"}
    assert edges(scan_fake(tmp_path, files)) == {}
    from fake_lang import FakeModule
    monkeypatch.setattr(FakeModule, "loose_names", True)
    assert ("A", "N.Foo", "dependency") in edges(scan_fake(tmp_path, files))


def test_scope_chain_finds_global_alias_with_scoped_name(tmp_path):
    graph = scan_fake(tmp_path, {
        "lib.fk": "type lib module\nglobal ns.helper id:lib\n",
        "user.fk": "type ns.User class\nscope ns.User\nref dependency helper call helper\n"
                   "ref dependency other.helper call helper\n",
    })
    assert ("ns.User", "lib", "dependency") in edges(graph)
    assert externals(graph, "ns.User") == ["other.helper"]


def test_alias_target_resolves_in_its_own_scope(tmp_path):
    from coderstanding.language import FileResult, Target
    from coderstanding.model import Graph, Node
    from coderstanding.resolver import Parsed, Resolver
    from fake_lang import FAKE
    graph = Graph()
    graph.add_node(Node("a.b.Vec", "Vec", "class", "fake", "x"))
    resolver = Resolver(tmp_path, graph)
    parsed = Parsed("y", FAKE, FileResult())
    resolver.global_aliases["a.V"] = (Target(name="b.Vec", scope="a"), parsed)
    assert resolver._global_alias("a.V").id == "a.b.Vec"
    assert resolver.resolve(Target(name="b.Vec"), parsed, use_aliases=False).id == ""


def test_self_calls_are_kept_as_a_self_edge(tmp_path):
    """Regression: the resolver used to drop every self reference."""
    graph = scan_fake(tmp_path, {
        "a.fk": "type A class\nref dependency A call helper\nref dependency A call helper\n"})
    self_edge = graph.edges[("A", "A", "dependency")]
    assert [(c.member, c.count) for c in self_edge.calls] == [("helper", 2)]
