from pathlib import Path

from coderstanding.language import FileContext
from coderstanding.languages.gdscript import GDScriptModule, declared_class_name
from coderstanding.languages.godot import GodotModule
from coderstanding.scan import scan_repo
from helpers import calls, edge, write_files

FIXTURE = Path(__file__).parent / "fixtures" / "godot_gdscript"
MODULES = [GDScriptModule(), GodotModule()]
WEAPON = "res://src/weapon.gd"


def scan_fixture():
    return scan_repo(FIXTURE, modules=MODULES)




def parse(tmp_path, source, rel="a.gd"):
    write_files(tmp_path, {rel: source})
    return GDScriptModule().parse(FileContext(tmp_path, rel))


def test_node_per_file_with_class_name_or_res_path():
    graph = scan_fixture()
    player, weapon = graph.nodes["Player"], graph.nodes[WEAPON]
    assert (player.kind, player.name, player.base) == ("class", "Player", "CharacterBody2D")
    assert (weapon.kind, weapon.name, weapon.lang) == ("script", "weapon.gd", "gdscript")
    assert graph.nodes["Enemy"].base == "res://src/base_actor.gd"


def test_inner_class_is_a_child_node():
    graph = scan_fixture()
    assert graph.nodes["Player.Buff"].kind == "inner"
    assert edge(graph, "Player.Buff", "res://src/weapon.gd", "dependency").detail == ["param",
                                                                                     "call"]


def test_extends_by_path_and_name():
    graph = scan_fixture()
    assert ("Enemy", "res://src/base_actor.gd", "generalization") in graph.edges
    assert ("Player", "CharacterBody2D", "generalization") in graph.nodes["Player"].external_refs


def test_preload_names_resolve_for_new_hints_and_calls():
    graph = scan_fixture()
    owned = edge(graph, "Player", WEAPON, "composition")  # weapon := Weapon.new(); spare = ...
    assert "field" in owned.detail and calls(owned) == {"fire": 1, "reload": 1}
    used = edge(graph, "Player", WEAPON, "dependency")
    assert {"load", "create", "return"} <= set(used.detail)
    assert calls(used) == {"new": 1, "reload": 1}  # var tmp := Weapon.new(); tmp.reload()


def test_inherited_preload_name_from_base_script():
    graph = scan_fixture()  # Tools is a const of base_actor.gd, used in enemy.gd
    assert calls(edge(graph, "Enemy", "res://src/tools.gd", "dependency")) == {"helper": 1}


def test_typed_export_is_association_and_static_call_is_dependency():
    graph = scan_fixture()
    assert edge(graph, "Player", "Enemy", "association").detail == ["export"]
    assert calls(edge(graph, "Player", "Enemy", "dependency")) == {"spawn_count": 1}


def test_autoload_call_and_signal_connect():
    graph = scan_fixture()
    game = "res://autoload/game.gd"
    assert calls(edge(graph, "Player", game, "dependency")) == {"add_score": 1}
    signal = edge(graph, "Player", game, "signal")
    assert signal.detail == ["score_changed"] and signal.auto == "signal score_changed → _on_score"
    assert "died → _on_died" in calls(edge(graph, "Player", "Enemy", "signal"))


def test_inferred_type_from_new_gives_calls(tmp_path):
    result = parse(tmp_path, (
        "extends Node\n"
        "const Acoustics := preload(\"res://acoustics.gd\")\n"
        "func f():\n"
        "\tvar a := Acoustics.new()\n"
        "\ta.play()\n"
        "\tvar b := load(\"res://b.gd\").new()\n"
        "\tb.go()\n"
        "\tvar c = Thing.new()\n"
        "\tc.run()\n"
    ))
    found = {(r.to.text, r.call) for r in result.refs if r.detail == "call"}
    assert found == {("Acoustics", "play"), ("res://b.gd", "go"), ("Thing", "run")}


def test_untyped_and_builtin_names_make_no_refs(tmp_path):
    result = parse(tmp_path, (
        "extends Node\n"
        "const MAX_HP := 10\n"
        "var n = 3\n"
        "func f(x, y: int) -> void:\n"
        "\tn.foo()\n"
        "\tx.bar()\n"
        "\tposition.x = MAX_HP\n"
        "\tvar s: String = \"\"\n"
    ))
    assert [r.to.text for r in result.refs] == ["Node"]


def test_generic_hints_and_casts(tmp_path):
    result = parse(tmp_path, (
        "extends Node\n"
        "var items: Array[Item] = []\n"
        "var table: Dictionary[String, Slot]\n"
        "func f():\n"
        "\tvar c = $X as Card\n"
        "\tif c is Deck: pass\n"
    ))
    assert {(r.to.text, r.kind, r.detail) for r in result.refs} == {
        ("Node", "generalization", ""), ("Item", "association", "field"),
        ("Slot", "association", "field"), ("Card", "dependency", "local"),
        ("Deck", "dependency", "local")}


def test_relative_preload_and_handler_names(tmp_path):
    result = parse(tmp_path, (
        "extends Node\n"
        "const Util := preload(\"util.gd\")\n"
        "func f():\n"
        "\tNet.joined.connect(Screen.on_join)\n"
        "\tNet.left.connect(_on_left.bind(1))\n"
    ), rel="src/a.gd")
    assert result.aliases["Util"].path == "res://src/util.gd"
    assert {r.call for r in result.refs if r.kind == "signal"} == {"joined → Screen.on_join",
                                                                  "left → _on_left"}


def test_abstract_flag_and_members(tmp_path):
    result = parse(tmp_path, ("@abstract\nclass_name Shape\nextends Node\nsignal moved\n"
                              "var size := 1\nfunc area() -> float:\n\treturn 0.0\n"))
    decl = result.declarations[0]
    assert decl.flags == ["abstract"] and decl.id == "Shape"
    assert [(m.name, m.kind) for m in decl.members] == [("moved", "signal"), ("size", "field"),
                                                         ("area", "method")]


def test_declared_class_name():
    assert declared_class_name("@tool\nclass_name Foo extends Node\n") == "Foo"
    assert declared_class_name("extends Node\n# class_name Nope\n") == ""


def test_from_member_function_lambda_inner_and_field(tmp_path):
    result = parse(tmp_path, """class_name A
extends Node
var held: Foo
const L = preload("res://l.gd")
class Inner:
\tfunc go(x: Bar):
\t\tUtil.run()
func fire():
\tvar f = func(): Other.hit()
\tf.call()
""")
    found = {(r.to.name or r.to.path, r.detail, r.call): r.from_member for r in result.refs}
    assert found[("Foo", "field", "")] == "<field>"
    assert found[("Bar", "param", "")] == "go"
    assert found[("Util", "call", "run")] == "go"
    assert found[("Other", "call", "hit")] == "fire"  # lambda counts as its enclosing func
    assert found[("res://l.gd", "load", "")] == "<init>"
    assert [r.from_member for r in result.refs if r.kind == "generalization"] == [None]


def test_private_inner_class_is_a_type_name(tmp_path):
    (tmp_path / "w.gd").write_text("extends Node\nvar _overlay := _Overlay.new()\n\n"
                                   "class _Overlay extends Node2D:\n\tpass\n")
    graph = scan_repo(tmp_path, use_cache=False)
    assert ("res://w.gd", "res://w.gd._Overlay", "composition") in graph.edges
    assert graph.nodes["res://w.gd._Overlay"].span and graph.nodes["res://w.gd._Overlay"].lines == 2


def test_calls_to_own_functions_are_internal_calls(tmp_path):
    write_files(tmp_path, {"a.gd": "class_name A\nextends Node\n\nfunc _ready():\n\thelper()\n"
                                   "\tself.other()\n\tprint(1)\n\nfunc helper(): pass\n"
                                   "func other(): pass\n"})
    graph = scan_repo(tmp_path, modules=[GDScriptModule()])
    own = graph.edges[("A", "A", "dependency")]
    assert {(c.from_member, c.member) for c in own.calls} == {("_ready", "helper"),
                                                              ("_ready", "other")}
