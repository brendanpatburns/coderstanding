"""The local server as the hosted page's helper: CORS for the hosted page's origin only (reads
and writes), write origin checks, and nothing left of the old push to the lab."""

import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from coderstanding.apps import App
from coderstanding.cli import build_parser
from coderstanding.hub import Hub
from coderstanding.server import HOST, Api, make_server
from helpers import scan_fake

HUB_ORIGIN = "https://hub.example"
FILES = {"game.fk": "type Game class entry\nref composition Player field\n",
         "player.fk": "type Player class\n"}


def start(hub):
    srv = make_server(hub, port=0, allow_origin=HUB_ORIGIN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://{HOST}:{srv.server_port}"


@pytest.fixture
def local(tmp_path):
    """A local server for one fake app: (base url, None, None)."""
    graph = scan_fake(tmp_path / "repo", FILES)
    api = Api(App("repo", tmp_path / "repo", tmp_path / "state"), graph=graph)
    srv, base = start(Hub(None, fixed={"repo": api}))
    yield base, None, None
    srv.shutdown()
    srv.server_close()


def send(base, path, body=None, method="PUT", headers=None):
    """(status, headers, json body) of a request; JSON content type unless headers say otherwise."""
    data = None if body is None else json.dumps(body).encode()
    request = Request(base + path, data=data, method=method,
                      headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urlopen(request) as response:
            text = response.read()
            return response.status, response.headers, json.loads(text) if text else None
    except HTTPError as error:
        return error.code, error.headers, json.loads(error.read() or b"null")


LAYOUT = {"items": [{"key": "Game", "x": 1, "y": 2}]}


def test_write_from_hub_origin_passes_with_cors_header(local):
    base, _, _ = local
    status, headers, _ = send(base, "/api/layout", LAYOUT, headers={"Origin": HUB_ORIGIN})
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == HUB_ORIGIN


@pytest.mark.parametrize("origin", ["http://evil.example", "https://hub.example.evil.com", "null",
                                    "http://127.0.0.1:1"])
def test_write_from_foreign_origin_is_403(local, origin):
    base, pushes, _ = local
    status, headers, body = send(base, "/api/layout", LAYOUT, headers={"Origin": origin})
    assert (status, body["error"]) == (403, "cross-site request refused")
    assert "Access-Control-Allow-Origin" not in headers


def test_write_without_origin_or_from_own_page_works(local):
    base, _, _ = local
    assert send(base, "/api/layout", LAYOUT)[0] == 200
    assert send(base, "/api/layout", LAYOUT, headers={"Origin": base})[0] == 200


def test_write_needs_json_content_type(local):
    base, _, _ = local
    status, _, body = send(base, "/api/layout", LAYOUT, headers={
        "Content-Type": "application/x-www-form-urlencoded"})
    assert status == 415 and "application/json" in body["error"]
    assert send(base, "/api/layout", LAYOUT, headers={"Content-Type": "text/plain"})[0] == 415


def test_foreign_host_name_refused(local):
    """DNS rebinding: another name pointed at 127.0.0.1 gets nothing."""
    base, _, _ = local
    status, _, _ = send(base, "/api/meta", method="GET", headers={"Host": "evil.example"})
    assert status == 403


def test_preflight_for_hub_origin(local):
    base, _, _ = local
    status, headers, _ = send(base, "/api/notes", method="OPTIONS", headers={
        "Origin": HUB_ORIGIN, "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Private-Network": "true"})
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == HUB_ORIGIN
    assert headers["Access-Control-Allow-Private-Network"] == "true"
    assert "PUT" in headers["Access-Control-Allow-Methods"]
    assert "Content-Type" in headers["Access-Control-Allow-Headers"]


def test_preflight_for_other_origin_refused(local):
    base, _, _ = local
    status, headers, _ = send(base, "/api/notes", method="OPTIONS",
                              headers={"Origin": "http://evil.example"})
    assert status == 403 and "Access-Control-Allow-Origin" not in headers


@pytest.mark.parametrize("path", ["/api/apps", "/api/view?app=repo", "/api/meta?app=repo",
                                  "/api/node/Game?app=repo", "/api/text?app=repo"])
def test_reads_carry_cors_only_for_hub_origin(local, path):
    """The hosted page reads everything from the helper, so every read is let in for its origin."""
    base, _, _ = local
    request = Request(base + path, headers={"Origin": HUB_ORIGIN})
    with urlopen(request) as response:
        assert response.headers["Access-Control-Allow-Origin"] == HUB_ORIGIN
    request = Request(base + path, headers={"Origin": "http://evil.example"})
    with urlopen(request) as response:
        assert "Access-Control-Allow-Origin" not in response.headers


def test_listing_is_just_the_apps_and_sync_is_gone(local):
    base, _, _ = local
    listing = send(base, "/api/apps", method="GET")[2]
    assert set(listing) == {"initial", "apps"}
    assert set(listing["apps"][0]) >= {"name", "scannedAt", "nodes", "scanning", "error"}
    assert "syncedAt" not in listing["apps"][0]
    assert send(base, "/api/sync", method="GET")[0] == 404  # no such route


def test_serve_cli_has_no_hosted_or_sync_options():
    args = build_parser().parse_args(["serve", "--allow-origin", "https://x.example"])
    assert args.allow_origin == "https://x.example"
    for gone in (["serve", "--read-only"], ["serve", "--no-sync"], ["sync"]):
        with pytest.raises(SystemExit):
            build_parser().parse_args(gone)
