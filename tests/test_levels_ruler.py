"""Depth ruler: saved level names/count, the clamp, per-level counts, names in the text headers."""
import pytest

from coderstanding.apps import App, AppError, rescan
from coderstanding.server import Api
from coderstanding.textout import format_graph
from coderstanding.views import compute_view, level_scale
from helpers import write_files
from test_tours import CODE
from test_tours_api import call, serve


@pytest.fixture
def desk(tmp_path):
    write_files(tmp_path / "repo", {"m.py": CODE})
    app = App("app", tmp_path / "repo", tmp_path / "repo" / ".coderstanding")
    graph = rescan(app)
    return app, graph


def view(app, graph, **kw):
    return compute_view(graph, app.load_notes(), **kw)


def test_defaults_are_current_behaviour(desk):
    app, graph = desk
    scale = view(app, graph, level=1)["scale"]
    assert scale["names"][:2] == ["Domains", "Level 1"] and scale["count"] == len(scale["names"]) - 1
    assert sum(scale["counts"][1:]) == view(app, graph, level=scale["count"])["counts"]["total"]
    assert app.level_settings() is None


def test_saved_names_count_and_clamp(desk):
    app, graph = desk
    deepest = level_scale(graph, app.load_notes())["count"]
    assert deepest > 1
    app.set_levels(1, ["Dom", "Everything"])
    notes = app.load_notes()
    assert level_scale(graph, notes) == {"count": 1, "names": ["Dom", "Everything"]}
    shown = view(app, graph, level=1)
    assert shown["scale"]["counts"][1] == shown["counts"]["total"]  # deeper classes count as level 1
    assert shown["counts"]["shown"] == shown["counts"]["total"]
    assert " L1 Everything " in format_graph("app", shown).splitlines()[0]


def test_counts_follow_show_toggles(desk):
    app, graph = desk
    base = view(app, graph, level=1)["scale"]["counts"]
    no_ext = view(app, graph, level=1, show={"external": False})["scale"]["counts"]
    assert sum(no_ext) <= sum(base)


def test_set_levels_rejects_bad_counts(desk):
    app, _ = desk
    for bad in (0, 99, "x"):
        with pytest.raises(AppError):
            app.set_levels(bad, [])


def test_cli_settings_levels(desk):
    app, graph = desk
    app.change_settings({"levels": "Domains, Apps ,Systems"})
    assert app.level_settings() == {"count": 2, "names": ["Domains", "Apps", "Systems"]}
    assert app.scan_settings() == {"hub_threshold": app.scan_settings()["hub_threshold"], "max_level": app.scan_settings()["max_level"]}  # levels is not a scan setting


def test_api_saves_levels(desk):
    app, graph = desk
    srv, base = serve(Api(app, graph=graph))
    try:
        got = call(base, "/api/levels?app=app", {"count": 2, "names": ["D", "One", "Two"]}, "PUT")
        assert got["names"] == ["D", "One", "Two"]
        assert call(base, "/api/meta?app=app")["scale"]["names"] == ["D", "One", "Two"]
        assert call(base, "/api/view?app=app&level=1")["scale"]["counts"][0] == 0
        call(base, "/api/levels?app=app", {"reset": True}, "PUT")
        assert app.level_settings() is None
    finally:
        srv.shutdown()
        srv.server_close()
