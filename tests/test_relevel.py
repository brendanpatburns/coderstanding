"""Opt-in scan overrides: `rescan --setting`, `--relevel [ai|all]`, `--reset-level-names`, and the hub's options."""
import json

import pytest

from coderstanding import cli
from coderstanding.apps import REGISTRY_ENV, AppError, find_app, register, relevel, rescan
from coderstanding.hub import scan_command, scan_options
from coderstanding.notes import AI_AUTHOR, NODE_PREFIX, node_key


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "apps.json"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("class Aa:\n    pass\n")
    (repo / "b.py").write_text("class Bb:\n    pass\n")
    register("p", repo, tmp_path / "data")
    app = find_app("p")
    rescan(app)
    notes = app.load_notes()
    notes.set(node_key("a.Aa"), AI_AUTHOR, {"level": 3})
    notes.set(node_key("b.Bb"), "Ann", {"level": 2, "hint": "keep me"}, ai=False)
    app.save_notes(notes)
    app.set_levels(3, ["D", "One", "Two", "Three"])
    return app


def levels_left(app):
    notes = app.load_notes()
    return {k: notes.value(k, "level") for k in notes.keys() if notes.has(k, "level")}


def test_setting_override_applies_once_and_is_not_saved(app):
    graph = rescan(app, {"max_level": "1"})
    assert {n.level for n in graph.nodes.values()} == {1}
    assert "max_level" not in app.saved_settings()
    assert app.scan_settings()["max_level"] == 5


def test_setting_override_is_validated(app):
    with pytest.raises(AppError, match="max_level"):
        rescan(app, {"max_level": "99"})
    with pytest.raises(AppError, match="unknown setting"):
        rescan(app, {"nope": "1"})


def test_plain_rescan_touches_no_level_work(app):
    before = app.notes_path.read_text(), app.settings_path.read_text()
    rescan(app)
    assert (app.notes_path.read_text(), app.settings_path.read_text()) == before
    assert not list(app.data.glob("notes.levels-*"))


def test_relevel_default_clears_only_ai_levels_and_keeps_names(app):
    done = relevel(app)
    assert done["cleared"] == 1
    assert levels_left(app) == {node_key("b.Bb"): 2}
    assert app.load_notes().value(node_key("b.Bb"), "hint") == "keep me"
    assert app.level_settings()["names"] == ["D", "One", "Two", "Three"]
    backup = [json.loads(line) for line in done["backup"].read_text().splitlines()]
    assert backup == [{"key": node_key("a.Aa"), "level": 3, "by": AI_AUTHOR, "ai": True}]


def test_relevel_all_clears_human_levels_too(app):
    done = relevel(app, "all")
    assert done["cleared"] == 2 and levels_left(app) == {}
    assert app.load_notes().value(node_key("b.Bb"), "hint") == "keep me"
    assert len(done["backup"].read_text().splitlines()) == 2


def test_reset_level_names_only_when_asked(app):
    relevel(app, "ai", reset_names=True)
    assert app.level_settings() is None


def test_relevel_with_nothing_to_clear_writes_no_backup(app):
    relevel(app, "all")
    done = relevel(app, "all")
    assert done == {"cleared": 0, "backup": None}


def test_relevel_rejects_unknown_scope(app):
    with pytest.raises(AppError):
        relevel(app, "everything")


def test_cli_rescan_flags(app, capsys):
    cli.main(["rescan", "p", "--setting", "max_level=2", "--relevel"])
    out = capsys.readouterr().out
    assert "cleared 1 level notes" in out and "level names kept" in out
    assert "max_level" not in app.saved_settings() and levels_left(app) == {node_key("b.Bb"): 2}
    cli.main(["rescan", "p", "--relevel", "all", "--reset-level-names"])
    assert levels_left(app) == {} and app.level_settings() is None


def test_cli_reset_names_needs_relevel(app):
    with pytest.raises(SystemExit):
        cli.main(["rescan", "p", "--reset-level-names"])
    assert app.level_settings() is not None


def test_hub_options_become_child_flags(app):
    options = scan_options({"settings": {"hub_threshold": "20"}, "relevel": "all", "resetLevelNames": True})
    command = scan_command(app, options)
    assert command[command.index("--setting") + 1] == "hub_threshold=20"
    assert command[command.index("--relevel") + 1] == "all" and "--reset-level-names" in command
    assert "--relevel" not in scan_command(app, scan_options({}))


@pytest.mark.parametrize("body", [{"relevel": "some"}, {"resetLevelNames": True},
                                  {"settings": {"hub_threshold": "-4"}}, {"settings": {"x": "1"}}])
def test_hub_rejects_bad_options(body):
    with pytest.raises(AppError):
        scan_options(body)
