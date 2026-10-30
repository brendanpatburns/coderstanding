"""Entry points by mechanism: Godot main / autoload, program main, script main."""

import json

from coderstanding.languages.cpp import CppModule
from coderstanding.languages.csharp import CSharpModule
from coderstanding.languages.godot import GodotModule
from coderstanding.languages.gdscript import GDScriptModule
from coderstanding.languages.javascript import JavaScriptModule
from coderstanding.languages.package_json import PackageJsonModule
from coderstanding.languages.python import PythonModule
from coderstanding.scan import scan_repo
from helpers import write_files


def scan(tmp_path, files, *modules):
    write_files(tmp_path, files)
    return scan_repo(tmp_path, modules=list(modules))


def flags(graph, node_id):
    return graph.nodes[node_id].flags


def test_godot_main_scene_script_and_autoload(tmp_path):
    graph = scan(tmp_path, {
        "project.godot": '[application]\nrun/main_scene="res://main.tscn"\n'
                         '[autoload]\nGame="*res://game.gd"\n',
        "main.tscn": '[gd_scene format=3]\n[ext_resource type="Script" path="res://main.gd" id="1"]\n'
                     '[node name="Main" type="Node"]\nscript = ExtResource("1")\n',
        "main.gd": "extends Node\n", "game.gd": "extends Node\n",
    }, GDScriptModule(), GodotModule())
    assert flags(graph, "res://main.tscn") == ["godot-main-scene", "entry"]
    assert flags(graph, "res://main.gd") == ["godot-main", "entry"]
    assert flags(graph, "res://game.gd") == ["autoload", "entry", "singleton"]
    assert graph.nodes["res://main.gd"].name == "main.gd"  # no class_name: the file name
    assert graph.nodes["res://main.tscn"].name == "main.tscn"
    assert {n.level for n in graph.nodes.values()} == {1}


def test_csharp_main_method_and_top_level_statements(tmp_path):
    graph = scan(tmp_path, {
        "Program.cs": "class Program { static void Main(string[] a) { } }\n",
        "Helper.cs": "class Helper { void Main() { } }\n",  # not static: not a program
        "Top/Start.cs": "System.Console.WriteLine(1);\n",
    }, CSharpModule())
    assert flags(graph, "Program") == ["program-main", "entry"]
    assert flags(graph, "Helper") == []
    assert flags(graph, "Top/Start.cs") == ["program-main", "entry"]


def test_c_main_is_program_main(tmp_path):
    graph = scan(tmp_path, {"app.c": "int main(void) { return 0; }\n",
                            "lib.c": "int helper(void) { return 1; }\n"}, CppModule())
    assert flags(graph, "app") == ["program-main", "entry"]
    assert flags(graph, "lib") == []


def test_python_main_guard_is_script_main(tmp_path):
    graph = scan(tmp_path, {"tool.py": "def run(): pass\nif __name__ == '__main__':\n    run()\n",
                            "lib.py": "def f(): pass\n"}, PythonModule())
    assert flags(graph, "tool") == ["script-main", "entry"]
    assert flags(graph, "lib") == []


def test_node_shebang_and_package_json_scripts(tmp_path):
    graph = scan(tmp_path, {
        "cli.js": "#!/usr/bin/env node\nconsole.log(1)\n",
        "server.js": "console.log(2)\n", "cmd/tool.js": "console.log(3)\n", "lib.js": "export const a = 1\n",
        "package.json": json.dumps({"bin": {"t": "./cmd/tool.js"},
                                    "scripts": {"start": "node server.js --port 1", "x": "echo hi"}}),
    }, JavaScriptModule(), PackageJsonModule())
    for name in ("cli.js", "server.js", "cmd/tool.js"):
        assert flags(graph, name) == ["script-main", "entry"], name
    assert flags(graph, "lib.js") == []


def test_tools_are_not_level_one_next_to_an_app_start(tmp_path):
    graph = scan(tmp_path, {
        "Program.cs": "class Program { static void Main() { } }\n",
        "tool.py": "if __name__ == '__main__':\n    pass\n",
    }, CSharpModule(), PythonModule())
    assert (graph.nodes["Program"].level, graph.nodes["tool"].level) == (1, 2)
