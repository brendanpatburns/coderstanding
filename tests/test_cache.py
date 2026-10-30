import json

from coderstanding import cache as cache_mod
from coderstanding.graph_json import graph_to_json
from coderstanding.languages.gdscript import GDScriptModule
from coderstanding.scan import scan_repo
from fake_lang import FAKE, FakeModule
from helpers import edge, write_files

FILES = {
    "a.fk": "type A class\nref composition B field\n",
    "b.fk": "type B class\nbase Node\n",
    "c.fk": "type C class\nref association A\n",
}


def scan(root, **kwargs):
    return scan_repo(root, modules=[FAKE], **kwargs)


def snapshot(graph):
    data = graph_to_json(graph)
    data.pop("scannedAt", None)
    return json.dumps(data, sort_keys=True)


class CountingModule(FakeModule):
    """Records which files were really parsed."""
    parsed = []

    def parse(self, ctx):
        CountingModule.parsed.append(ctx.rel_path)
        return super().parse(ctx)


def counted_scan(root):
    CountingModule.parsed = []
    scan_repo(root, modules=[CountingModule()])
    return sorted(CountingModule.parsed)


def test_warm_scan_equals_cold_scan(tmp_path):
    write_files(tmp_path, FILES)
    cold = snapshot(scan(tmp_path))
    assert snapshot(scan(tmp_path)) == cold
    assert snapshot(scan(tmp_path, use_cache=False)) == cold


def test_only_changed_file_is_reparsed(tmp_path):
    write_files(tmp_path, FILES)
    assert counted_scan(tmp_path) == ["a.fk", "b.fk", "c.fk"]
    assert counted_scan(tmp_path) == []
    write_files(tmp_path, {"b.fk": "type B class\n"})
    assert counted_scan(tmp_path) == ["b.fk"]


def test_deleted_file_is_dropped_from_graph_and_cache(tmp_path):
    write_files(tmp_path, FILES)
    scan(tmp_path)
    (tmp_path / "c.fk").unlink()
    graph = scan(tmp_path)
    assert "C" not in graph.nodes
    assert len(list((tmp_path / ".coderstanding" / "cache").glob("*.json"))) == 2


def test_version_bump_invalidates(tmp_path, monkeypatch):
    write_files(tmp_path, FILES)
    counted_scan(tmp_path)
    monkeypatch.setattr(cache_mod, "SCANNER_VERSION", cache_mod.SCANNER_VERSION + 1)
    assert counted_scan(tmp_path) == ["a.fk", "b.fk", "c.fk"]


def test_base_script_change_invalidates_dependants(tmp_path):
    write_files(tmp_path, {
        "tools.gd": "class_name Tools\nfunc helper(): pass\n",
        "base.gd": "class_name Base\nconst T := preload(\"res://tools.gd\")\n",
        "mid.gd": "class_name Mid\nextends \"res://base.gd\"\n",
        "child.gd": "class_name Child\nextends \"res://mid.gd\"\nfunc f():\n\tT.helper()\n",
    })
    modules = [GDScriptModule()]
    graph = scan_repo(tmp_path, modules=modules)
    assert edge(graph, "Child", "Tools", "dependency")
    write_files(tmp_path, {"base.gd": "class_name Base\n"})
    graph = scan_repo(tmp_path, modules=modules)  # warm cache, base changed two levels up
    assert not [key for key in graph.edges if key[:2] == ("Child", "Tools")]
    assert snapshot(graph) == snapshot(scan_repo(tmp_path, modules=modules, use_cache=False))


def test_parallel_equals_serial(tmp_path):
    write_files(tmp_path, FILES)
    serial = snapshot(scan(tmp_path, use_cache=False, workers=1))
    pooled = snapshot(scan(tmp_path, use_cache=False, workers=2, parallel_min=1))
    assert pooled == serial


def test_gitignore_written_and_cache_dir_override(tmp_path, monkeypatch):
    write_files(tmp_path, FILES)
    scan(tmp_path)
    assert (tmp_path / ".coderstanding" / ".gitignore").read_text() == "cache/\ngraph.json\n"
    elsewhere = tmp_path.parent / (tmp_path.name + "-cache")
    monkeypatch.setenv(cache_mod.CACHE_DIR_ENV, str(elsewhere))
    scan(tmp_path)
    assert list(elsewhere.glob("*.json"))


def test_scanner_version_dropped_old_caches():
    assert cache_mod.SCANNER_VERSION >= 2  # 2 = Ref.from_member
