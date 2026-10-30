from coderstanding.godot_files import read_config, read_scene
from coderstanding.languages.gdscript import GDScriptModule
from coderstanding.languages.godot import GodotModule
from coderstanding.scan import scan_repo
from helpers import calls, edge, write_files
from test_gdscript import FIXTURE, MODULES

MAIN = "res://scenes/main.tscn"
HUD = "res://scenes/hud.tscn"


def test_autoload_flags_and_global_name():
    graph = scan_repo(FIXTURE, modules=MODULES)
    assert graph.nodes["res://autoload/game.gd"].flags == ["autoload", "entry", "singleton"]
    assert graph.nodes["res://autoload/game.gd"].level == 1


def test_main_scene_and_its_root_script_are_entries():
    graph = scan_repo(FIXTURE, modules=MODULES)
    assert graph.nodes[MAIN].flags == ["godot-main-scene", "entry"]
    assert graph.nodes["res://src/main.gd"].flags == ["godot-main", "entry"]


def test_scene_node_owns_scripts_and_sub_scenes():
    graph = scan_repo(FIXTURE, modules=MODULES)
    scene = graph.nodes[MAIN]
    assert (scene.kind, scene.name, scene.base, scene.lang) == ("scene", "main.tscn", "Node", "godot")
    children = {e.to for e in graph.edges.values()
                if e.from_id == MAIN and e.kind == "composition"}
    assert children == {"res://src/main.gd", "Player", "Enemy", HUD}
    assert edge(graph, MAIN, HUD, "composition").detail == ["scene-child"]


def test_connection_is_signal_from_listener_script_to_emitter():
    graph = scan_repo(FIXTURE, modules=MODULES)
    signal = edge(graph, "Player", "Enemy", "signal")
    assert "died" in signal.detail and "died → _on_died" in calls(signal)
    # An emitter without a script is its engine type: an outside ref.
    assert ("res://src/main.gd", "Button", "signal") in \
        graph.nodes["res://src/main.gd"].external_refs


def test_script_that_instantiates_a_scene_depends_on_it():
    graph = scan_repo(FIXTURE, modules=MODULES)
    used = edge(graph, "Player", HUD, "dependency")
    assert set(used.detail) == {"load", "create"} and calls(used) == {"instantiate": 1}


def test_main_scene_by_uid(tmp_path):
    write_files(tmp_path, {
        "project.godot": '[application]\nrun/main_scene="uid://abc"\n',
        "s/start.tscn": ('[gd_scene format=3 uid="uid://abc"]\n'
                         '[ext_resource type="Script" path="res://a.gd" id="1"]\n'
                         '[node name="Start" type="Node"]\nscript = ExtResource("1")\n'),
        "a.gd": "extends Node\n",
    })
    graph = scan_repo(tmp_path, modules=[GDScriptModule(), GodotModule()])
    assert graph.nodes["res://s/start.tscn"].flags == ["godot-main-scene", "entry"]
    assert graph.nodes["res://a.gd"].flags == ["godot-main", "entry"]


def test_scene_listener_without_script_falls_back_to_scene(tmp_path):
    write_files(tmp_path, {
        "project.godot": "",
        "x.tscn": ('[ext_resource type="Script" path="res://e.gd" id="1"]\n'
                   '[node name="X" type="Node"]\n'
                   '[node name="E" type="Node" parent="."]\nscript = ExtResource("1")\n'
                   '[connection signal="hit" from="E" to="." method="_on_hit"]\n'),
        "e.gd": "class_name Emitter\nextends Node\n",
    })
    graph = scan_repo(tmp_path, modules=[GDScriptModule(), GodotModule()])
    assert ("res://x.tscn", "Emitter", "signal") in graph.edges


def test_read_config_and_scene_tolerate_multiline_values():
    config = read_config('a=1\n[s]\nk="v"\nm={\n"x": 1\n}\n[t]\nn=2\n')
    assert config == {"": {"a": "1"}, "s": {"k": '"v"', "m": "{"}, "t": {"n": "2"}}
    scene = read_scene('[node name="R" type="Node"]\n[node name="C" parent="."]\n'
                       '[node name="D" parent="C"]\n')
    assert [n.path for n in scene.nodes] == [".", "C", "C/D"]
