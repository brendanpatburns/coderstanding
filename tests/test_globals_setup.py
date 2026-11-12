"""Globals (how code and tests reach them) and `setup <test file>`."""
import shutil
from pathlib import Path

from coderstanding.cli import main
from coderstanding.languages.csharp import CSharpModule
from coderstanding.languages.gdscript import GDScriptModule
from coderstanding.languages.godot import GodotModule
from coderstanding.scan import scan_repo
from coderstanding.testcode import function_role
from coderstanding.views import globals_list, setup_of
from coderstanding.notes import Notes
from helpers import write_files

FILES = {
    "project.godot": '[autoload]\nMedia="*res://src/media.gd"\n',
    "src/media.gd": "extends Node\nfunc play(): pass\n",
    "src/net.gd": "class_name Net\nextends Node\n",
    "src/Fast.cs": "using Godot;\n[GlobalClass]\npublic partial class Fast : RefCounted {}\n",
    "tests/base.gd": "extends Node\nfunc _net(): pass\nfunc before_all(): pass\n",
    "tests/test_a.gd": 'extends "res://tests/base.gd"\n'
                       "func before_each():\n"
                       '\tvar a = get_tree().root.get_node("Media")\n'
                       '\tvar b = $"/root/Media"\n'
                       "\tvar n = Net.new()\n"
                       '\tvar own = get_node("Child")\n'
                       "func _prepare_server(): pass\n"
                       "func test_x(): _net()\n",
    "tests/test_b.gd": "extends Node\nfunc _prepare_server(): pass\nfunc _login(): pass\n"
                       "func test_y(): pass\n",
    "tests/test_c.gd": "extends Node\nfunc _prepare_server(): pass\nfunc _login(): pass\n"
                       "func test_z(): pass\n",
}


def scanned(tmp_path):
    write_files(tmp_path, FILES)
    return scan_repo(tmp_path, modules=[GDScriptModule(), CSharpModule(), GodotModule()])


def test_function_roles():
    assert [function_role(n) for n in ("test_x", "testFoo", "before_each", "setUp", "_login")] \
        == ["test", "test", "hook", "hook", "helper"]


def test_globals_list_kinds_and_lookups(tmp_path):
    rows = {r["name"]: r for r in globals_list(scanned(tmp_path))}
    assert {n: r["kind"] for n, r in rows.items()} == {
        "Media": "autoload", "Net": "class_name", "Fast": "global-class"}
    assert rows["Media"]["tests"] == ['get_tree().root.get_node("Media")', '$"/root/Media"']
    assert rows["Media"]["code"] == []  # `get_node("Child")` is a relative lookup: not a global


def test_setup_lists_helpers_globals_and_missing(tmp_path):
    graph = scanned(tmp_path)
    data = setup_of(graph, Notes(), "tests/test_a.gd")
    assert [r["name"] for r in data["hooks"]] == ["before_each"]
    assert [r["name"] for r in data["helpers"]] == ["_prepare_server"]
    assert data["bases"][0]["helpers"] == ["_net"] and data["bases"][0]["hooks"] == ["before_all"]
    globals_here = {g["name"]: g["here"] for g in data["globals"]}
    assert globals_here["Media"][0] == 'get_tree().root.get_node("Media")'
    assert globals_here["Net"] == []
    assert [(m["kind"], m["name"], m["count"]) for m in data["missing"]] == [("helper", "_login", 2)]


def test_setup_cli_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "game"
    write_files(repo, FILES)
    main(["add", "game", str(repo)])
    main(["rescan", "game"])
    capsys.readouterr()
    main(["setup", "game", "test_a.gd"])
    out = capsys.readouterr().out
    assert "hooks:" in out and "base base.gd (tests/base.gd): before_all (hook), _net" in out
    assert 'Media  autoload res://src/media.gd · here: get_tree().root.get_node("Media")' in out
    assert "Net  class_name src/net.gd · by name" in out
    assert "helper _login  in 2/2 sibling tests" in out
    main(["graph", "game", "1"])
    graph_out = capsys.readouterr().out
    assert 'Media  autoload res://src/media.gd · tests: get_tree().root.get_node("Media")' in graph_out
    main(["outline", "game", "Media"])
    assert "global: Media  autoload res://src/media.gd" in capsys.readouterr().out
