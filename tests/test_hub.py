"""Multi-app server: /api/apps, ?app= scoping, Rescan and "scan a folder" (temp registry only)."""

import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from coderstanding.apps import REGISTRY_ENV, DATA_HOME_ENV, find_app, register
from coderstanding.hub import Hub
from coderstanding.server import HOST, Api, make_server


def make_repo(root, classes):
    root.mkdir(parents=True)
    for name in classes:
        (root / f"{name.lower()}.py").write_text(f"class {name}:\n    pass\n")
    return root


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "apps.json"))
    monkeypatch.setenv(DATA_HOME_ENV, str(tmp_path / "share"))
    register("alpha", make_repo(tmp_path / "alpha", ["Aa", "Ab"]), tmp_path / "alpha-data")
    register("beta", make_repo(tmp_path / "beta", ["Bb"]), tmp_path / "beta-data")
    srv = make_server(Hub(Api), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://{HOST}:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def call(base, path, data=None):
    request = Request(base + path, method="POST" if data is not None else "GET",
                      data=json.dumps(data).encode() if data is not None else None,
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request) as response:
            return json.loads(response.read())
    except HTTPError as error:
        return {"status": error.code, **json.loads(error.read())}


def wait_scanned(base, name):
    for _ in range(300):
        entry = next(a for a in call(base, "/api/apps")["apps"] if a["name"] == name)
        if not entry["scanning"] and (entry["error"] or entry["nodes"]):
            return entry
        time.sleep(0.1)
    raise AssertionError(f"scan did not finish: {entry}")


def test_apps_listing_and_scoping(base, tmp_path):
    first = call(base, "/api/apps")
    assert [a["name"] for a in first["apps"]] == ["alpha", "beta"]
    assert first["initial"] is None
    # each app is scoped by ?app=; no app means the first one
    has = lambda app, node: "error" not in call(base, f"/api/node/{node}?app={app}")
    assert has("alpha", "aa.Aa") and not has("beta", "aa.Aa") and has("beta", "bb.Bb")
    assert call(base, "/api/meta")["repo"] == "alpha"
    assert call(base, "/api/meta?app=nope")["status"] == 404
    listed = {a["name"]: a for a in call(base, "/api/apps")["apps"]}
    assert listed["alpha"]["nodes"] == 4 and listed["alpha"]["data"] == str(tmp_path / "alpha-data")
    assert listed["alpha"]["scannedAt"]


def test_rescan_writes_to_data_folder_not_repo(base, tmp_path):
    call(base, "/api/view?app=beta")
    (tmp_path / "beta" / "extra.py").write_text("class Extra:\n    pass\n")
    assert call(base, "/api/rescan?app=beta", {})["apps"]
    assert wait_scanned(base, "beta")["nodes"] == 4
    assert (tmp_path / "beta-data" / "graph.json").exists()
    assert not (tmp_path / "beta" / ".coderstanding").exists()


def test_rescan_error_is_reported(base, tmp_path):
    for path in (tmp_path / "beta").glob("*.py"):
        path.unlink()
    assert "apps" in call(base, "/api/rescan?app=beta", {})
    assert "no supported files" in wait_scanned(base, "beta")["error"]


def test_scan_a_folder_registers_and_scans(base, tmp_path):
    repo = make_repo(tmp_path / "gamma-repo", ["Gg"])
    call(base, "/api/apps", {"name": "gamma", "path": str(repo)})
    assert wait_scanned(base, "gamma")["nodes"] == 2
    assert find_app("gamma").data == tmp_path / "share" / "gamma"
    assert not (repo / ".coderstanding").exists()
    assert "error" not in call(base, "/api/node/gg.Gg?app=gamma")


@pytest.mark.parametrize("body,message", [
    ({"name": "x", "path": "relative/dir"}, "absolute"),
    ({"name": "x", "path": "/nonexistent/zzz"}, "no such folder"),
    ({"name": "alpha", "path": "/tmp"}, "already taken"),
    ({"name": "bad name!", "path": "/tmp"}, "name:"),
])
def test_scan_a_folder_rejects_bad_input(base, tmp_path, body, message):
    answer = call(base, "/api/apps", body)
    assert answer["status"] == 400 and message in answer["error"]
    assert [a["name"] for a in call(base, "/api/apps")["apps"]] == ["alpha", "beta"]


def test_scan_a_folder_rejects_file_and_empty_folder(base, tmp_path):
    (tmp_path / "plain.txt").write_text("hi")
    (tmp_path / "empty").mkdir()
    assert "not a folder" in call(base, "/api/apps", {"name": "p", "path": str(tmp_path / "plain.txt")})["error"]
    assert "no supported files" in call(base, "/api/apps", {"name": "e", "path": str(tmp_path / "empty")})["error"]


def test_cross_site_write_refused(base):
    request = Request(base + "/api/apps", method="POST", data=b"{}",
                      headers={"Origin": "http://evil.example"})
    with pytest.raises(HTTPError) as refused:
        urlopen(request)
    assert refused.value.code == 403
