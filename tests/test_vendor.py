import subprocess

import pytest

from coderstanding import apps, vendor
from coderstanding.cache import write_gitignore
from coderstanding.languages.javascript import JavaScriptModule
from coderstanding.scan import scan_repo
from fake_lang import FAKE
from helpers import write_files

ONE_TYPE = "type {name} class\n"


@pytest.mark.parametrize("rel_path", [
    "deploy/web-extra/livekit-client-2.22.4.umd.js", "lib/jquery-3.7.js", "a/vendor/x.js",
    "third_party/x.js", "app.min.js"])
def test_vendor_names(rel_path):
    assert vendor.is_vendor(rel_path)


@pytest.mark.parametrize("rel_path", ["src/player.js", "src/v2/player.js", "notes-final.js"])
def test_own_names(rel_path):
    assert not vendor.is_vendor(rel_path)


def test_graph_scan_skips_vendor_wide_and_excluded_files(tmp_path):
    write_files(tmp_path, {
        "own.js": "class Own {}\n",
        "lib-1.2.3.js": "class Versioned {}\n",
        "wide.js": "class Wide {}\n// " + "x" * 2000 + "\n",
        "gen/out.js": "class Generated {}\n",
    })
    graph = scan_repo(tmp_path, modules=[JavaScriptModule()], exclude=["gen/*"])
    assert {node.name for node in graph.nodes.values()} == {"own.js", "Own"}  # file node + class


def test_wide_or_versioned_code_outside_web_files_is_kept(tmp_path):
    """One long line in C, Python or GDScript is a big expression, not a minified library."""
    write_files(tmp_path, {
        "wide.fk": ONE_TYPE.format(name="Wide") + "# " + "x" * 2000 + "\n",
        "level_1.2.fk": ONE_TYPE.format(name="Versioned"),
    })
    graph = scan_repo(tmp_path, modules=[FAKE])
    assert set(graph.nodes) == {"Wide", "Versioned"}


def test_exclude_reaches_the_text_index(tmp_path):
    write_files(tmp_path, {"own.fk": ONE_TYPE.format(name="Own"), "gen/out.fk": "needle_text\n"})
    graph = scan_repo(tmp_path, modules=[FAKE], exclude=["gen/*"])
    assert "gen/out.fk" not in graph.names["files"]


def test_exclude_setting_is_parsed_and_defaults_empty():
    assert apps.parse_setting("exclude", "a/*, *.gen.js") == ["a/*", "*.gen.js"]
    assert apps.SETTINGS["exclude"][1] == []


def test_gitignore_hides_itself_and_upgrades_old_files(tmp_path):
    write_gitignore(tmp_path)
    lines = (tmp_path / ".coderstanding" / ".gitignore").read_text().splitlines()
    assert lines == ["cache/", "graph.json", ".gitignore"]
    old = tmp_path / ".coderstanding" / ".gitignore"
    old.write_text("cache/\ngraph.json\n")
    write_gitignore(tmp_path)
    assert old.read_text().splitlines() == ["cache/", "graph.json", ".gitignore"]


def test_include_scans_ignored_folder_in_graph_and_text_index_and_exclude_still_applies(tmp_path):
    write_files(tmp_path, {".gitignore": "gen/\n", "own.fk": ONE_TYPE.format(name="Own"),
                           "gen/a.fk": ONE_TYPE.format(name="Lifted"),
                           "gen/skip.fk": ONE_TYPE.format(name="Skipped")})
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert "gen/a.fk" not in scan_repo(tmp_path, modules=[FAKE]).names["files"]
    graph = scan_repo(tmp_path, modules=[FAKE], include=["gen/"], exclude=["gen/skip.fk"])
    assert {"Own", "Lifted"} <= {node.name for node in graph.nodes.values()}
    assert "Skipped" not in {node.name for node in graph.nodes.values()}
    assert "gen/a.fk" in graph.names["files"]


def test_include_setting_is_parsed_and_defaults_empty():
    assert apps.parse_setting("include", "generated/, out/pseudo/") == ["generated/", "out/pseudo/"]
    assert apps.SETTINGS["include"][1] == []
