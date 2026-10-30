import os
import shutil
from pathlib import Path

import pytest

from coderstanding.cli import main
from coderstanding.notes import load_notes

FIXTURE = Path(__file__).parent / "fixtures" / "godot_gdscript"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "game"
    shutil.copytree(FIXTURE, repo)
    main(["add", "game", str(repo)])
    return repo


def run(capsys, *argv):
    main(list(argv))
    return capsys.readouterr().out.splitlines()


def test_add_writes_registry(app, tmp_path):
    assert "game" in (tmp_path / "home/.config/coderstanding/apps.json").read_text()


def test_graph_by_name_and_path(app, capsys):
    capsys.readouterr()
    lines = run(capsys, "graph", "game", "2")
    assert lines[0] == "game L2 all 7/11"
    assert "Player > game.gd*: [dependency, signal] {add_score, score_changed → _on_score}" in lines
    assert "Player > fancy [addon]: [association, dependency] {follow, new}" in lines
    assert run(capsys, "graph", str(app), "2")[1:] == lines[1:]
    assert (app / ".coderstanding" / "graph.json").exists()


def test_never_rescans_by_itself_but_rescan_does(app, capsys):
    run(capsys, "graph", "game")
    graph_json = app / ".coderstanding" / "graph.json"
    built = graph_json.stat().st_mtime
    os.utime(app / "src" / "player.gd", (built + 10, built + 10))
    run(capsys, "graph", "game")
    assert graph_json.stat().st_mtime == built
    run(capsys, "rescan", "game")
    assert graph_json.stat().st_mtime > built


def test_zoom_and_node(app, capsys):
    lines = run(capsys, "zoom", "game", "main.tscn", "game")
    assert lines[0] == "game zoom main.tscn [scene]>game.gd* L2 all"
    assert "Player > game.gd*: [dependency, signal] {add_score, score_changed → _on_score}" in lines
    assert lines[-1].startswith("out: ")
    lines = run(capsys, "node", "game", "Player")
    assert lines[:2] == ["game node Player L3 all",
                         "[class] src/player.gd:1 L2 46 lines (file), 13 members"]
    assert "Player > Buff: [composition]" in lines


def test_ambiguous_name_lists_candidates(app, capsys):
    with pytest.raises(SystemExit) as error:
        main(["node", "game", "main"])
    assert "ambiguous: res://scenes/main.tscn, res://src/main.gd" in str(error.value)


def test_set_and_curate(app, capsys, tmp_path):
    lines = run(capsys, "curate", "game")
    assert lines[0] == "game curate 10 to do (id file level in/out -missing {calls})"
    assert any(l.startswith("Player src/player.gd L2 1/6 -level,domain,hint") for l in lines)
    run(capsys, "set", "game", "Player", "level=1", "--by", "you")
    batch = tmp_path / "batch.txt"
    batch.write_text('Player level=4 domain=Game hint="the hero"\n'
                     '# comment\n'
                     'Player>Enemy hint="hunts"\n')
    lines = run(capsys, "set", "game", "--file", str(batch))
    assert lines == ["set 2 by claude (AI draft)", "node:Player: kept your level"]
    notes = load_notes(app / ".coderstanding" / "notes.jsonl")
    assert notes.get("node:Player", "level") == (1, "you")
    assert notes.get("node:Player", "hint") == ("the hero", "claude")
    assert notes.get("edge:Player→Enemy", "hint") == ("hunts", "claude")
    lines = run(capsys, "curate", "game")
    assert lines[0].startswith("game curate 9 ") and not any("Player " in l for l in lines)
    graph = run(capsys, "graph", "game", "1")
    assert "Player > Enemy: hunts {died → _on_died, spawn_count}" not in graph  # Enemy is L2
    assert any(l.startswith("main.tscn [scene] > Player: [composition]") for l in graph)


def test_level_zero_domains(app, capsys):
    run(capsys, "set", "game", "Player", "domain=Game")
    run(capsys, "set", "game", "Enemy", "domain=Game")
    run(capsys, "set", "game", "game", "domain=Core")
    lines = run(capsys, "graph", "game", "0")
    assert "Game ~> Core (via 2): [dependency, signal] {add_score, score_changed → _on_score}" in lines


def test_data_folder_keeps_repo_clean(app, capsys, tmp_path):
    shutil.rmtree(app / ".coderstanding", ignore_errors=True)
    data = tmp_path / "data"
    run(capsys, "rescan", str(app), "--data", str(data))
    assert (data / "graph.json").exists() and not (app / ".coderstanding").exists()


def test_bad_set_value(app):
    with pytest.raises(SystemExit) as error:
        main(["set", "game", "Player", "level=high"])
    assert "level=high" in str(error.value)


def test_short_name_rules():
    from coderstanding.apps import find_id
    from coderstanding.notes import Notes
    from coderstanding.scan import scan_repo
    graph = scan_repo(FIXTURE)
    notes = Notes()
    assert find_id(graph, notes, "src/main.gd") == "res://src/main.gd"
    assert find_id(graph, notes, "main.tscn") == "res://scenes/main.tscn"
    assert find_id(graph, notes, "player.gd") == "Player"  # outer class over its inner one
    assert find_id(graph, notes, "buff") == "Player.Buff"


def add_tests_to(repo):
    (repo / "tests").mkdir()
    (repo / "tests" / "test_player.gd").write_text(
        "extends Node\nfunc test_hit():\n\tvar p := Player.new()\n\tp.hit()\n")
    (repo / "tests" / "test_main.gd").write_text(
        'extends Node\nconst Main := preload("res://scenes/main.tscn")\n')


def test_tests_command_direct_and_deep(app, capsys):
    add_tests_to(app)
    run(capsys, "rescan", "game")
    assert run(capsys, "tests", "game", "Player") == ["game tests Player 1", "tests/test_player.gd"]
    deep = run(capsys, "tests", "game", "Player", "--deep")
    assert deep == ["game tests Player deep 2", "tests/test_player.gd",
                    "tests/test_main.gd (via main.tscn)"]
    graph = run(capsys, "graph", "game", "5")
    assert not any("test_player" in line for line in graph)
    assert any("test_player" in line for line in run(capsys, "graph", "game", "5", "--tests"))
    node = run(capsys, "node", "game", "Player")
    assert "tests: tests/test_player.gd" in node


def test_warnings_command(app, capsys):
    lines = run(capsys, "warnings", "game")
    assert lines[0].startswith("game warnings: unused ")
    assert all(line.split(":")[0].split()[0] in ("unused", "god", "cycle", "unsure", "unresolved")
               for line in lines[1:])


def test_set_review_shows_in_node(app, capsys):
    run(capsys, "set", "game", "Player", r"review=split A into B\nand C")
    notes = load_notes(app / ".coderstanding" / "notes.jsonl")
    assert notes.get("node:Player", "review") == ("split A into B\nand C", "claude")
    node = run(capsys, "node", "game", "Player")
    assert "review: split A into B and C" in node


def test_set_status_tags_rules(app, capsys):
    run(capsys, "set", "game", "Player", "status=fragile", "tags=hero, core",
        r"rules=never free in _ready\nkeep hp >= 0")
    notes = load_notes(app / ".coderstanding" / "notes.jsonl")
    assert notes.get("node:Player", "tags") == (["hero", "core"], "claude")
    assert notes.get("node:Player", "rules")[0] == "never free in _ready\nkeep hp >= 0"
    node = run(capsys, "node", "game", "Player")
    assert node[1].endswith("[fragile] #hero #core")
    assert "rules: never free in _ready" in node and "  keep hp >= 0" in node
    zoom = run(capsys, "zoom", "game", "main.tscn", "Player")
    assert zoom[1:3] == ["rules Player: never free in _ready", "  keep hp >= 0"]
    with pytest.raises(SystemExit) as error:
        main(["set", "game", "Player", "status=shaky"])
    assert "status must be one of" in str(error.value)


def test_old_graph_version_asks_for_a_rescan(app, capsys):
    run(capsys, "graph", "game")
    graph_json = app / ".coderstanding" / "graph.json"
    graph_json.write_text('{"version": 1, "nodes": [], "edges": []}')
    with pytest.raises(SystemExit, match="coderstanding rescan game"):
        main(["graph", "game"])
    run(capsys, "rescan", "game")
    assert run(capsys, "graph", "game")[0].startswith("game L1 all ")
    assert '"version": 2' in graph_json.read_text()


def test_node_functions(app, capsys):
    lines = run(capsys, "node", "game", "Player", "--functions")
    assert lines[0] == "game node Player L3 all functions"
    assert "Player#_ready > weapon.gd: new, fire, reload ×2" in lines
    assert "Player (fields) > Buff: [composition]" in lines
    assert lines[-1].startswith("no calls: ")


def test_view_command_matches_shared_text_and_sets_function_notes(app, capsys):
    run(capsys, "set", "game", "Player#_ready", 'hint=starts up', "--by", "ana")
    lines = run(capsys, "view", "game", "--level", "2", "--expand", "Player",
                "--select", "Player#_ready")
    assert lines[0] == ("$ coderstanding view game --level 2 --expand Player "
                        "--select 'Player#_ready'")
    assert lines[1] == "game L2 all 7/11"
    assert lines[lines.index("selected Player#_ready:") + 1] == "hint: starts up"
    notes = load_notes(app / ".coderstanding" / "notes.jsonl")
    assert notes.get("node:Player#_ready", "hint") == ("starts up", "ana")
    assert not notes.is_ai("node:Player#_ready", "ana")
    zoomed = run(capsys, "view", "game", "--zoom", "Player", "Enemy")
    assert zoomed[0] == "$ coderstanding view game --level 1 --zoom Player Enemy"
    assert zoomed[1].startswith("game zoom Player>Enemy")


def test_settings_round_trip_and_stale(app, capsys):
    assert run(capsys, "settings", "game") == ["game settings hub_threshold=auto"]
    assert run(capsys, "settings", "game", "hub_threshold=1") == ["game settings hub_threshold=1"]
    run(capsys, "rescan", "game")
    assert run(capsys, "settings", "game") == ["game settings hub_threshold=1"]
    header = run(capsys, "graph", "game")[0]
    assert "(hubs ≥ 1: " in header and "[hub " in header
    run(capsys, "rescan", "game", "--data", str(app / "other"))
    assert "hubs" not in run(capsys, "graph", "game", "--data", str(app / "other"))[0]
    run(capsys, "settings", "game", "hub_threshold=off")
    run(capsys, "rescan", "game")  # settings apply at the next rescan
    assert "hubs" not in run(capsys, "graph", "game")[0]


def test_hub_in_node_line_and_notes_level_wins(app, capsys):
    run(capsys, "settings", "game", "hub_threshold=1")
    run(capsys, "rescan", "game")
    lines = run(capsys, "node", "game", "Player")
    assert "[hub " in lines[1]
    run(capsys, "set", "game", "Player", "level=3")
    assert "L3 [hub " in run(capsys, "node", "game", "Player")[1]


def test_hub_threshold_parsing(app, capsys, tmp_path):
    main(["add", "g2", str(app), "--data", str(tmp_path / "d2"), "--hub-threshold", "30"])
    capsys.readouterr()
    assert run(capsys, "settings", "g2") == ["g2 settings hub_threshold=30"]
    with pytest.raises(SystemExit):
        main(["add", "g3", str(app), "--hub-threshold", "lots"])
    with pytest.raises(SystemExit):
        main(["settings", "g2", "bogus=1"])
    with pytest.raises(SystemExit):
        main(["settings", "g2", "hub_threshold=-3"])
    main(["scan", str(app), "--hub-threshold", "off", "-o", str(tmp_path / "g.json")])


def test_warnings_all_flag(app, capsys):
    assert run(capsys, "warnings", "game", "--all")[0].startswith("game warnings: ")


def test_view_via_round_trip(app, capsys):
    lines = run(capsys, "view", "game", "--zoom", "Player", "Enemy", "--via", "Buff")
    assert lines[0].endswith("--zoom Player Enemy --via Player.Buff")
    assert "--via" not in run(capsys, "view", "game", "--zoom", "Player", "Enemy")[0]
    assert run(capsys, "zoom", "game", "Player", "Enemy", "--via", "Buff")[0].startswith("game zoom")


def test_rules_add_list_remove_and_apply(app, capsys, tmp_path):
    (app / "lonely.gd").write_text("class_name Lonely\nextends Node\n", encoding="utf-8")
    run(capsys, "rescan", "game")
    assert "unused 1" in run(capsys, "warnings", "game")[0]
    assert run(capsys, "rules", "game") == ["game rules 0"]
    run(capsys, "rules", "game", "add", "**", "ignore=unused,cycle", "lang=gdscript")
    run(capsys, "rules", "game", "add", "zzz/**", "ignore=all")
    assert run(capsys, "rules", "game") == ["game rules 2", "1. ** ignore=unused,cycle lang=gdscript",
                                            "2. zzz/** ignore=all"]
    before = run(capsys, "warnings", "game", "--all")
    assert "unused: Lonely (ignored: rule ** unused)" in before
    assert run(capsys, "warnings", "game")[0].startswith("game warnings: unused 0")
    run(capsys, "rules", "game", "remove", "1")
    assert run(capsys, "rules", "game") == ["game rules 1", "1. zzz/** ignore=all"]
    assert "unused 1" in run(capsys, "warnings", "game")[0]
    with pytest.raises(SystemExit):
        main(["rules", "game", "remove", "9"])
    with pytest.raises(SystemExit):
        main(["rules", "game", "add", "x/**", "ignore=bogus"])
    assert run(capsys, "settings", "game") == ["game settings hub_threshold=auto"]  # rules not a scan setting


def test_scan_with_output_keeps_cache_out_of_repo(tmp_path, capsys):
    repo = tmp_path / "readonly"
    shutil.copytree(FIXTURE, repo)
    main(["scan", str(repo), "-o", str(tmp_path / "out/graph.json")])
    assert not (repo / ".coderstanding").exists()
    assert any((tmp_path / "out/cache").iterdir())
