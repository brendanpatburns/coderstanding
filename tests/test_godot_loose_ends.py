"""Godot loose ends (DESIGN 12a): signal-unconnected, signal-unemitted, bad-handler,
autoload-unused, and the emit reading they rest on."""

from coderstanding.language import FileContext
from coderstanding.languages.csharp import CSharpModule
from coderstanding.languages.gdscript import GDScriptModule
from coderstanding.languages.godot import GodotModule
from coderstanding.model import EMIT_DETAIL
from coderstanding.scan import scan_repo
from helpers import edge, write_files

MODULES = [GDScriptModule(), GodotModule(), CSharpModule()]
LOOSE = ("signal-unconnected", "signal-unemitted", "bad-handler", "autoload-unused")
PROJECT = "[autoload]\n\nBus=\"*res://bus.gd\"\n"


def scan(tmp_path, files):
    write_files(tmp_path, {"project.godot": PROJECT, **files})
    return scan_repo(tmp_path, modules=MODULES)


def loose(graph):
    """{node id: {kind: names}} for the four kinds."""
    return {i: {w["kind"]: w.get("names", []) for w in n.warnings if w["kind"] in LOOSE}
            for i, n in graph.nodes.items()
            if any(w["kind"] in LOOSE for w in n.warnings)}


def test_reader_emit_forms(tmp_path):
    write_files(tmp_path, {"a.gd": (
        "extends Node\nsignal hit\nvar other: Foo\n"
        "func f(name):\n\thit.emit()\n\tself.hit.emit(1)\n\temit_signal(\"hit\")\n"
        "\temit_signal(name)\n\tother.done.emit()\n\tBus.ping.emit()\n")})
    result = GDScriptModule().parse(FileContext(tmp_path, "a.gd"))
    emits = [(r.to.id or r.to.name, r.call) for r in result.refs if r.detail == EMIT_DETAIL]
    assert emits == [("res://a.gd", "hit")] * 3 + [("res://a.gd", "*"), ("Foo", "done"),
                                                  ("Bus", "ping")]


def test_signal_unconnected(tmp_path):
    graph = scan(tmp_path, {"a.gd": "class_name A\nextends Node\nsignal hit\nsignal quiet\n"
                                    "func f():\n\thit.emit()\n"})
    assert loose(graph) == {"A": {"signal-unconnected": ["hit"]}}


def test_signal_unemitted(tmp_path):
    graph = scan(tmp_path, {
        "a.gd": "class_name A\nextends Node\nsignal hit\nsignal died\n"
                "func f():\n\tdied.emit()\n",
        "b.gd": "class_name B\nextends Node\nvar a: A\n"
                "func _ready():\n\ta.hit.connect(_on_hit)\n\ta.died.connect(_on_hit)\n"
                "func _on_hit():\n\tpass\n"})
    assert loose(graph) == {"A": {"signal-unemitted": ["hit"]}}


def test_connected_and_emitted_across_files_is_clean(tmp_path):
    graph = scan(tmp_path, {
        "a.gd": "class_name A\nextends Node\nsignal hit\nfunc f():\n\thit.emit()\n",
        "b.gd": "class_name B\nextends Node\nvar a: A\n"
                "func _ready():\n\ta.hit.connect(_on_hit)\nfunc _on_hit():\n\tpass\n",
        "c.gd": "class_name C\nextends A\nsignal own\n"
                "func g():\n\town.emit()\n\town.connect(_on_own)\nfunc _on_own():\n\tpass\n",
        "bus.gd": "extends Node\nsignal ping\n",
        "d.gd": "class_name D\nextends Node\nfunc _ready():\n\tBus.ping.connect(_p)\n"
                "\tBus.ping.emit()\nfunc _p():\n\tpass\n"})
    assert loose(graph) == {}


def test_emit_in_subclass_and_dynamic_names_count(tmp_path):
    graph = scan(tmp_path, {
        "a.gd": "class_name A\nextends Node\nsignal hit\nsignal late\n"
                "func g():\n\tpass\n",
        "b.gd": "class_name B\nextends A\nfunc f(n):\n\thit.emit()\n\temit_signal(n)\n"})
    # hit is emitted in a subclass; emit_signal(n) may emit any signal; nothing connects them
    assert loose(graph) == {"A": {"signal-unconnected": ["hit", "late"]}}


def test_scene_connection_connects_and_bad_handler(tmp_path):
    scene = ('[gd_scene load_steps=2 format=3]\n'
             '[ext_resource type="Script" path="res://a.gd" id="1"]\n'
             '[ext_resource type="Script" path="res://b.gd" id="2"]\n'
             '[node name="Root" type="Node"]\nscript = ExtResource("2")\n'
             '[node name="Child" type="Node" parent="."]\nscript = ExtResource("1")\n'
             '[connection signal="hit" from="Child" to="." method="_on_hit"]\n'
             '[connection signal="hit" from="Child" to="." method="_on_missing"]\n')
    graph = scan(tmp_path, {
        "a.gd": "class_name A\nextends Node\nsignal hit\nfunc f():\n\thit.emit()\n",
        "b.gd": "class_name B\nextends Node\nfunc _on_hit():\n\tpass\n",
        "s.tscn": scene})
    assert loose(graph) == {"B": {"bad-handler": ["hit → _on_missing"]}}


def test_bad_handler_in_code_skips_lambdas_variables_and_inherited(tmp_path):
    graph = scan(tmp_path, {
        "base.gd": "class_name Base\nextends Node\nfunc _on_base():\n\tpass\n",
        "b.gd": "class_name B\nextends Base\nvar btn: Button\n"
                "func _ready():\n\tvar cb := func(): pass\n"
                "\tbtn.pressed.connect(_on_base)\n\tbtn.pressed.connect(cb)\n"
                "\tbtn.pressed.connect(func(): pass)\n\tbtn.pressed.connect(Other.go)\n"
                "\tbtn.pressed.connect(_on_nope)\n"
                "\tbtn.connect(\"pressed\", Callable(self, \"_on_gone\"))\n"})
    assert loose(graph) == {"B": {"bad-handler": ["pressed → _on_gone", "pressed → _on_nope"]}}


def test_autoload_unused_and_used(tmp_path):
    graph = scan(tmp_path, {"bus.gd": "extends Node\nfunc ping():\n\tpass\n",
                            "a.gd": "class_name A\nextends Node\nfunc f():\n\tpass\n"})
    assert loose(graph) == {"res://bus.gd": {"autoload-unused": []}}
    graph = scan(tmp_path, {"a.gd": "class_name A\nextends Node\nfunc f():\n\tBus.ping()\n"})
    assert loose(graph) == {}


def test_autoload_emit_is_a_typed_edge(tmp_path):
    graph = scan(tmp_path, {"bus.gd": "extends Node\nsignal ping\n",
                            "a.gd": "class_name A\nextends Node\nfunc f():\n\tBus.ping.emit()\n"})
    assert edge(graph, "A", "res://bus.gd", "dependency").detail == [EMIT_DETAIL]


def test_csharp_signals(tmp_path):
    graph = scan(tmp_path, {
        "A.cs": ("public partial class A : Node\n{\n"
                 "  [Signal] public delegate void HitEventHandler();\n"
                 "  [Signal] public delegate void QuietEventHandler();\n"
                 "  [Signal] public delegate void SeenEventHandler();\n"
                 "  public void F() { EmitSignal(SignalName.Hit); EmitSignal(nameof(Seen)); }\n"
                 "  public void G() { Seen += OnSeen; }\n  void OnSeen() {}\n}\n")})
    assert loose(graph) == {"A": {"signal-unconnected": ["Hit"]}}


def test_non_godot_nodes_get_no_loose_end_warnings(tmp_path):
    graph = scan(tmp_path, {"a.py": "print(1)\n"})
    assert loose(graph) == {}


def test_emit_callable_and_constructor_bodies_are_read(tmp_path):
    graph = scan(tmp_path, {
        "a.gd": "class_name A\nextends Node\nsignal done\nsignal late\n"
                "func _init() -> void:\n\tvar b := Button.new()\n\tb.pressed.connect(done.emit)\n"
                "\tlate.emit()\n",
        "b.gd": "class_name B\nextends Node\nvar a: A\nfunc _ready():\n\ta.done.connect(_on_x)\n"
                "\ta.late.connect(_on_x)\nfunc _on_x():\n\tpass\n"})
    assert loose(graph) == {}


def test_warning_text_names_the_signals():
    from coderstanding.checks import warning_text
    assert warning_text({"kind": "signal-unemitted", "names": ["a", "b", "c", "d"], "count": 4}) \
        == "signal-unemitted a, b, c +1"
