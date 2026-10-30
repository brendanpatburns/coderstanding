"""GDScript reaches C# `[GlobalClass]` classes by bare name (not namespaced, not plain classes)."""
from coderstanding.languages.csharp import CSharpModule
from coderstanding.languages.gdscript import GDScriptModule
from coderstanding.scan import scan_repo
from helpers import calls, edge, write_files

FILES = {
    "project.godot": "config_version=5\n",
    "fast/SimFast.cs": "using Godot;\nnamespace Game.Fast;\n[GlobalClass]\n"
                       "public partial class SimFast : RefCounted { public int Step() => 1; }\n",
    "fast/Hidden.cs": "namespace Game.Fast;\npublic class Hidden { public int Run() => 1; }\n",
    "sim.gd": "class_name Sim\nvar _fast: SimFast\nvar _h: Hidden\n"
              "func go():\n\t_fast = SimFast.new()\n\t_fast.Step()\n",
}


def test_gdscript_resolves_global_class_csharp(tmp_path):
    write_files(tmp_path, FILES)
    graph = scan_repo(tmp_path, modules=[GDScriptModule(), CSharpModule()])
    fast = edge(graph, "Sim", "Game.Fast.SimFast", "composition")
    assert "Step" in calls(fast)
    assert ("Sim", "Game.Fast.Hidden", "composition") not in graph.edges
