"""The app manager (viewer modal, `coderstanding remove|rename`): details, remove with or without
the data folder, rename. Always on an isolated registry."""

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from coderstanding.apps import (REGISTRY_ENV, AppError, find_app, load_registry, register,
                                remove_app, rename_app, rescan)
from coderstanding.cli import main
from coderstanding.hub import Hub
from coderstanding.server import HOST, Api, make_server

ORIGIN = "https://hub.example"


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "apps.json"))


def scanned(tmp_path, name="one", data=None):
    """A registered, scanned app with notes, a tour, specs and settings in its data folder."""
    repo = tmp_path / f"{name}-repo"
    repo.mkdir()
    (repo / "a.py").write_text("class Aa:\n    pass\n")
    register(name, repo, data or tmp_path / f"{name}-data")
    app = find_app(name)
    rescan(app)
    (app.data / "layout.json").write_text('{"items": []}')
    (app.notes_path).write_text(json.dumps({"key": "node:a.Aa", "by": "me", "hint": "x"}) + "\n")
    (app.data / "tours").mkdir()
    (app.data / "tours" / "walk.md").write_text("---\ntitle: Walk\n---\n")
    (app.data / "specs").mkdir()
    (app.data / "settings.json").write_text("{}")
    return app


def test_details_count_notes_tours_and_size(tmp_path):
    app = scanned(tmp_path)
    listing = Hub(Api).listing(details=True)["apps"][0]
    assert listing["notes"] == 1 and listing["tours"] == 1 and listing["nodes"] == 2
    assert listing["graphBytes"] == app.graph_path.stat().st_size and not listing["staleScanner"]
    assert listing["files"]["own"] == ["notes.jsonl", "specs", "tours", "settings.json"]
    assert "notes" not in Hub(Api).listing()["apps"][0]  # the picker's listing stays light


def test_old_scanner_map_is_described_and_flagged(tmp_path):
    app = scanned(tmp_path)
    data = json.loads(app.graph_path.read_text())
    app.graph_path.write_text(json.dumps({**data, "scannerVersion": 1}))
    entry = Hub(Api).listing(details=True)["apps"][0]
    assert (entry["scannerVersion"], entry["staleScanner"], entry["nodes"]) == (1, True, 2)


def test_remove_from_list_keeps_every_file(tmp_path):
    app = scanned(tmp_path)
    before = sorted(p.name for p in app.data.iterdir())
    result = remove_app("one")
    assert "one" not in load_registry() and result["deleted"] == []
    assert sorted(p.name for p in app.data.iterdir()) == before


def test_delete_data_keeps_notes_tours_specs_and_everything_outside(tmp_path):
    app = scanned(tmp_path)
    (tmp_path / "beside.txt").write_text("mine")
    result = remove_app("one", delete_data=True)
    assert set(result["deleted"]) == {"graph.json", "cache", "layout.json"}
    assert sorted(p.name for p in app.data.iterdir()) == ["notes.jsonl", "settings.json", "specs", "tours"]
    assert (tmp_path / "beside.txt").exists() and (app.root / "a.py").exists()


def test_delete_notes_too_empties_and_removes_the_folder(tmp_path):
    app = scanned(tmp_path)
    remove_app("one", delete_data=True, delete_notes=True)
    assert not app.data.exists() and (app.root / "a.py").exists()


def test_delete_notes_needs_delete_data(tmp_path):
    scanned(tmp_path)
    with pytest.raises(AppError):
        remove_app("one", delete_notes=True)
    assert "one" in load_registry()


def test_in_repo_data_only_as_dot_coderstanding(tmp_path):
    repo = tmp_path / "r"
    (repo / "cache").mkdir(parents=True)  # the repo's own cache folder
    (repo / "a.py").write_text("class Aa:\n    pass\n")
    register("odd", repo, repo)  # data = the repo itself
    with pytest.raises(AppError):
        remove_app("odd", delete_data=True)
    assert (repo / "cache").exists() and "odd" in load_registry()
    register("dot", repo)  # default data: <repo>/.coderstanding
    rescan(find_app("dot"))
    remove_app("dot", delete_data=True)
    assert not (repo / ".coderstanding").exists() and (repo / "cache").exists()


def test_rename_moves_the_name_only(tmp_path):
    app = scanned(tmp_path)
    scanned(tmp_path, "two")
    assert rename_app("one", "uno") == "uno"
    assert find_app("uno").data == app.data and "one" not in load_registry()
    with pytest.raises(AppError):
        rename_app("uno", "two")  # taken
    with pytest.raises(AppError):
        rename_app("uno", "bad name")


def test_cli_remove_and_rename(tmp_path, capsys):
    app = scanned(tmp_path)
    main(["rename", "one", "uno"])
    main(["remove", "uno", "--delete-data"])
    out = capsys.readouterr().out
    assert "one -> uno" in out and "deleted in" in out and "kept in" in out
    assert (app.notes_path).exists() and not app.graph_path.exists()


def call(base, path, method="GET", body=None, origin=None):
    headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
    request = Request(base + path, method=method, headers=headers,
                      data=None if body is None else json.dumps(body).encode())
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def base():
    srv = make_server(Hub(Api), port=0, allow_origin=ORIGIN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://{HOST}:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_api_details_rename_and_remove(tmp_path, base):
    app = scanned(tmp_path)
    assert call(base, "/api/apps?details=1")[1]["apps"][0]["tours"] == 1
    status, body = call(base, "/api/apps/one", "PATCH", {"name": "uno"}, ORIGIN)
    assert status == 200 and [a["name"] for a in body["apps"]] == ["uno"]
    status, body = call(base, "/api/apps/uno?data=1", "DELETE", origin=ORIGIN)
    assert status == 200 and body["apps"] == [] and "graph.json" in body["deleted"]
    assert app.notes_path.exists()


def test_api_refuses_other_origins_and_unknown_apps(tmp_path, base):
    scanned(tmp_path)
    assert call(base, "/api/apps/one?data=1", "DELETE", origin="http://evil.example")[0] == 403
    assert call(base, "/api/apps/one", "PATCH", {"name": "x"}, "http://evil.example")[0] == 403
    assert call(base, "/api/apps/nope", "DELETE")[0] == 400
    assert "one" in load_registry()
