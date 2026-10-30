from pathlib import Path

from coderstanding.language import FileContext

from coderstanding.languages.csharp import CSharpModule
from coderstanding.scan import scan_repo
from helpers import edge

FIXTURES = Path(__file__).parent / "fixtures"


def scan(name):
    return scan_repo(FIXTURES / name, modules=[CSharpModule()])



def has_edge(graph, from_id, to, kind):
    return (from_id, to, kind) in {e.key for e in graph.edges.values()}


def test_declarations_and_flags():
    graph = scan("csharp_basic")
    nodes = graph.nodes
    assert nodes["Geometry.Shapes.IShape"].kind == "interface"
    assert "abstract" in nodes["Geometry.Shapes.Polygon"].flags
    assert "static" in nodes["Geometry.App.MathUtil"].flags
    assert nodes["Geometry.Shapes.Square.Corner"].kind == "inner"
    assert nodes["Geometry.App.Settings.Mode"].kind == "enum"
    assert nodes["Geometry.App.Settings.Point"].kind == "record"


def test_partial_class_merges_across_files():
    canvas = scan("csharp_basic").nodes["Geometry.App.Canvas"]
    assert "partial" in canvas.flags
    assert {m.name for m in canvas.members} >= {"_renderer", "_palette", "Draw", "Pick"}


def test_base_list_realization_and_generalization():
    graph = scan("csharp_basic")
    edge(graph, "Geometry.Shapes.Circle", "Geometry.Shapes.IShape", "realization")
    edge(graph, "Geometry.Shapes.Square", "Geometry.Shapes.IShape", "realization")
    edge(graph, "Geometry.Shapes.Square", "Geometry.Shapes.Polygon", "generalization")


def test_composition_vs_association():
    graph = scan("csharp_basic")
    canvas = "Geometry.App.Canvas"
    renderer = edge(graph, canvas, "Geometry.App.Renderer", "composition")  # field initializer
    assert "call" in renderer.detail and [c.member for c in renderer.calls] == ["Paint"]
    edge(graph, canvas, "Geometry.App.Logger", "association")  # property, only held
    edge(graph, canvas, "Geometry.Shapes.IShape", "association")
    assert not has_edge(graph, canvas, "Geometry.Shapes.IShape", "composition")
    # assigned from a constructor parameter, not created
    edge(graph, canvas, "Geometry.App.Palette", "association")
    edge(graph, canvas, "Geometry.Shapes.Circle", "composition")  # List<Circle> = new ...


def test_generics_stripped_and_outside_names_kept_external():
    graph = scan("csharp_basic")
    canvas = graph.nodes["Geometry.App.Canvas"]
    assert not any("<" in e.to for e in graph.edges.values())
    assert any(e.to == "List" for e in canvas.external_refs.values())


def test_dependencies_param_return_local_create_static_call():
    graph = scan("csharp_basic")
    canvas = "Geometry.App.Canvas"
    assert "param" in edge(graph, canvas, "Geometry.Shapes.Square", "dependency").detail
    assert "return" in edge(graph, canvas, "Geometry.Shapes.IShape", "dependency").detail
    circle = edge(graph, canvas, "Geometry.Shapes.Circle", "dependency")
    assert circle.detail == ["create"]
    assert "local" in edge(graph, canvas, "Geometry.Shapes.Square.Corner", "dependency").detail
    util = edge(graph, canvas, "Geometry.App.MathUtil", "dependency")
    assert util.calls[0].member == "Clamp" and "call" in util.detail


def test_event_subscription_is_a_signal():
    signal = edge(scan("csharp_basic"), "Geometry.App.Canvas", "Geometry.App.Button", "signal")
    assert signal.detail == ["Clicked"]
    assert signal.auto == "Clicked -> OnClicked"


def test_singleton_hint():
    nodes = scan("csharp_basic").nodes
    assert "singleton" in nodes["Geometry.App.Settings"].flags
    assert "singleton" not in nodes["Geometry.App.Canvas"].flags


def test_godot_get_node_signal_and_load():
    graph = scan("godot_csharp")
    player = graph.nodes["Game.Player"]
    assert [m.name for m in player.members if m.kind == "signal"] == ["Hit"]
    assert player.base == "CharacterBody3D"
    get_node = edge(graph, "Game.Player", "Game.Health", "dependency")
    assert get_node.calls[0].member == "GetNode"
    edge(graph, "Game.Player", "Game.Hud", "dependency")
    loads = [e for e in graph.nodes["Game.Player"].external_refs.values()
             if "load" in e.detail]
    assert {e.to for e in loads} == {"bullet.tscn", "data/stats.tres"}


def from_members(tmp_path, source):
    (tmp_path / "A.cs").write_text(source)
    result = CSharpModule().parse(FileContext(tmp_path, "A.cs"))
    return {(r.to.name, r.detail, r.call): r.from_member for r in result.refs}


def test_from_member_per_enclosing_member(tmp_path):
    found = from_members(tmp_path, """
class A : Base {
    Foo held;
    Bar init = Make.Build();
    void Run(Baz p) {
        Util.Go();
        void Local() { Deep.Dig(); }
    }
    int Prop { get { return Calc.Value(); } }
}""")
    assert found[("Base", "", "")] is None
    assert found[("Foo", "field", "")] == "<field>"
    assert found[("Baz", "param", "")] == "Run"
    assert found[("Util", "call", "Go")] == "Run"
    assert found[("Deep", "call", "Dig")] == "Run"  # local function: its containing method
    assert found[("Calc", "call", "Value")] == "Prop"
    assert found[("Make", "call", "Build")] == "<init>"
