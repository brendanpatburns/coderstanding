"""The settings table (app_settings.py): the `settings` command, its errors, tracks and the guide."""

import json

import pytest

from coderstanding import app_settings, guide
from coderstanding.apps import AppError, find_app
from coderstanding.cli import main

PROVEN = {"name": "Proven", "of": {"files": "game/**"}, "done": {"tag": "proven"}}
TESTED_BY_HAND = {"name": "Checked", "done": {"tag": "checked"}}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "game"
    repo.mkdir()
    (repo / "a.py").write_text("class A:\n    pass\n", encoding="utf-8")
    main(["add", "game", str(repo), "--data", str(repo / ".coderstanding"), "--no-scan"])
    return find_app("game")


def saved_tracks(app):
    return app.saved_settings().get("tracks")


def test_tracks_set_add_replace_and_remove(app, capsys):
    main(["settings", "game", "tracks=" + json.dumps([PROVEN])])
    assert saved_tracks(app) == [PROVEN]
    main(["settings", "game", "tracks+=" + json.dumps(TESTED_BY_HAND)])
    assert [track["name"] for track in saved_tracks(app)] == ["Proven", "Checked"]
    changed = {**PROVEN, "done": {"tag": "other"}}
    main(["settings", "game", "tracks+=" + json.dumps(changed)])
    assert [track["name"] for track in saved_tracks(app)] == ["Checked", "Proven"]
    assert saved_tracks(app)[1]["done"] == {"tag": "other"}
    main(["settings", "game", "tracks-=Checked"])
    assert [track["name"] for track in saved_tracks(app)] == ["Proven"]
    assert "tracks=" in capsys.readouterr().out.splitlines()[-1]


def test_bad_track_aborts_and_leaves_the_file_unchanged(app):
    app.change_settings({"tracks": json.dumps([PROVEN])})
    before = app.settings_path.read_text()
    for changes in ([("tracks+", '{"name": "Bad"}'), ("tracks+", "not json")],
                    [("max_level", "7"), ("tracks+", '{"name": "Bad"}')],
                    [("tracks-", "Missing")]):
        with pytest.raises(AppError):
            app.change_settings(changes)
        assert app.settings_path.read_text() == before


def test_unknown_setting_lists_every_table_setting(app):
    with pytest.raises(AppError) as error:
        app.change_settings({"nope": "1"})
    for name in app_settings.NAMES:
        assert name in str(error.value)
    assert "tracks" in str(error.value) and "library" in str(error.value)


def test_show_prints_saved_lists_too(app, capsys):
    main(["settings", "game", "domains+=tools/*=Tools"])
    capsys.readouterr()
    main(["settings", "game"])
    assert "domains=" in capsys.readouterr().out


def test_every_setting_is_in_the_guide_from_the_table():
    text = guide.guide_text()
    for setting in app_settings.SETTINGS_TABLE:
        assert f"`{setting.syntax}`" in text
    assert guide.with_settings(guide.assets.docs_dir().joinpath("AI_GUIDE.md").read_text(encoding="utf-8")) == \
        guide.assets.docs_dir().joinpath("AI_GUIDE.md").read_text(encoding="utf-8")  # the committed block is current
