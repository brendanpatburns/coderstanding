from pathlib import Path

from coderstanding.model import Edge, Graph, Location, Node
from coderstanding.scan import scan_repo
from coderstanding.stack import read_stack, split_image
from fake_lang import FAKE
from helpers import edge, scan_fake
from test_gdscript import FIXTURE, MODULES

MISC = Path(__file__).parent / "fixtures" / "stack_misc"


def deps_by_id(stack):
    return {f"{d['kind']}:{d['name']}": d for d in stack["deps"]}


def test_godot_project_and_languages():
    stack = scan_repo(FIXTURE, modules=MODULES).stack
    assert stack["godot"] == [{
        "file": "project.godot", "name": "Fixture Game", "version": "4.5",
        "renderer": "forward_plus", "csharp": False, "main_scene": "res://scenes/main.tscn",
        "autoloads": {"Game": "res://autoload/game.gd"}}]
    assert stack["languages"] == {"gdscript": 8, "godot": 2}


def test_addon_is_one_outside_node_with_the_refs_into_it():
    graph = scan_repo(FIXTURE, modules=MODULES)
    node = graph.nodes["addon:fancy"]
    assert (node.kind, node.namespace, node.name) == ("external", "addon", "fancy")
    assert not any(n.file.startswith("addons/") for n in graph.nodes.values()
                   if n.kind != "external")
    assert edge(graph, "Player", "addon:fancy", "association").detail == ["field"]  # PhantomCam
    used = edge(graph, "Player", "addon:fancy", "dependency")  # preload("res://addons/...")
    assert {"load", "create"} <= set(used.detail)
    addon = deps_by_id(graph.stack)["addon:fancy"]
    assert (addon["version"], addon["title"], addon["author"]) == ("1.2", "Fancy Cam", "Someone")
    assert addon["used_by"] == ["Player"] and addon["refs"] == 3


def test_godot_api_is_counted_not_listed():
    graph = scan_repo(FIXTURE, modules=MODULES)
    api = deps_by_id(graph.stack)["godot-api:godot-api"]
    assert api["refs"] > 0 and "Player" in api["used_by"]
    assert "godot-api" not in graph.nodes and graph.stack["unresolved"]["refs"] == 0


def test_project_files_of_other_stacks():
    stack = scan_repo(MISC, modules=[FAKE]).stack
    assert stack["dotnet"] == [{
        "file": "game/Game.csproj", "sdk": "Godot.NET.Sdk/4.5.0", "framework": "net8.0",
        "packages": [{"name": "Newtonsoft.Json", "version": "13.0.3"},
                     {"name": "Serilog", "version": ""}],
        "project_refs": ["../lib/Lib.csproj"]}]
    deps = deps_by_id(stack)
    versions = {k: d["version"] for k, d in deps.items()}
    assert versions == {
        "nuget:Newtonsoft.Json": "13.0.3", "nuget:Serilog": "3.1.1",
        "npm:react": "^18.3.1", "npm:@scope/ui": "1.0.0", "npm:vitest": "2.0.0",
        "pip:numpy": "==2.1.0", "pip:requests": ">=2.31", "pip:PyYAML": ">=6",
        "pip:pytest": "",
        "image:node": "22-slim", "image:registry.local:5000/base/runtime": "",
        "image:nginx": "1.29-alpine", "image:postgres": "16",
        "submodule:vendor/lib": "",
    }
    assert deps["npm:vitest"]["dev"] is True
    assert deps["submodule:vendor/lib"]["url"] == "https://example.com/lib.git"


def test_split_image():
    assert split_image("python:3.12-slim") == ("python", "3.12-slim")
    assert split_image("host:5000/img") == ("host:5000/img", "")
    assert split_image("img@sha256:abc") == ("img", "")


def outside_ref(node, label, line=1):
    node.add_external(Edge(node.id, label, "dependency", ["call"],
                           at=[Location(node.file, line)]))


def test_outside_refs_map_to_packages():
    graph = Graph()
    cs = Node("Save", "Save", "class", "csharp", "a.cs", imports=["System", "Serilog.Core"])
    py = Node("tool", "tool", "module", "python", "t.py")
    js = Node("app", "app", "module", "javascript", "a.js")
    for node in (cs, py, js):
        graph.add_node(node)
    outside_ref(cs, "Newtonsoft.Json.JsonConvert")
    outside_ref(py, "numpy.linalg")
    outside_ref(py, "yaml")  # PyYAML's import name differs: stays unresolved
    outside_ref(js, "@scope/ui/button")
    read_stack(MISC, graph)
    assert ("Save", "nuget:Newtonsoft.Json", "dependency") in graph.edges
    assert graph.edges[("Save", "nuget:Serilog", "dependency")].detail == ["import"]
    assert ("tool", "pip:numpy", "dependency") in graph.edges
    assert ("app", "npm:@scope/ui", "dependency") in graph.edges
    assert graph.nodes["npm:@scope/ui"].kind == "external"
    assert "npm:react" not in graph.nodes  # unused packages stay in the stack block only
    assert graph.stack["unresolved"] == {"refs": 1, "top": [("yaml", 1)]}


def test_resolver_keeps_file_imports_on_nodes(tmp_path):
    graph = scan_fake(tmp_path, {"a.fk": "using Lib.Core\nusing Other\ntype A class\n"})
    assert graph.nodes["A"].imports == ["Lib.Core", "Other"]


def test_python_standard_library_is_not_unresolved():
    graph = Graph()
    py = Node("tool", "tool", "module", "python", "t.py")
    graph.add_node(py)
    for label in ("json", "pathlib.Path", "str", "mystery"):
        outside_ref(py, label)
    read_stack(MISC, graph)
    assert graph.stack["unresolved"] == {"refs": 1, "top": [("mystery", 1)]}
