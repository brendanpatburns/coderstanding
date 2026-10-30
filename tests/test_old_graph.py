"""A graph.json from another scanner version is rescanned, and a server error is always a JSON
answer."""

import json
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from coderstanding.apps import App, AppError, load_graph, rescan
from coderstanding.hub import Hub
from coderstanding.server import HOST, Api, make_server


@pytest.fixture
def app(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("class Aa:\n    pass\n")
    app = App("old", repo, tmp_path / "data")
    rescan(app)
    return app


def edit_graph(app, **changes):
    data = json.loads(app.graph_path.read_text())
    data.update(changes)
    app.graph_path.write_text(json.dumps(data))


def get(base, path):
    try:
        with urlopen(base + path) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def serve(hub):
    srv = make_server(hub, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://{HOST}:{srv.server_port}"


def test_an_api_error_is_a_json_500(app, monkeypatch):
    srv, base = serve(Hub(None, fixed={"old": Api(app)}))
    monkeypatch.setattr(Api, "meta", lambda self: 1 / 0)
    try:
        status, body = get(base, "/api/meta?app=old")
        assert status == 500 and "division" in body["error"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_local_mode_loads_another_scanner_version_without_rescanning(app):
    edit_graph(app, scannerVersion=1)
    assert load_graph(app).scanner_version == 1
    assert json.loads(app.graph_path.read_text())["scannerVersion"] == 1  # untouched
    edit_graph(app, version=99)
    with pytest.raises(AppError, match="coderstanding rescan"):
        load_graph(app)
