"""Server routes: spin the server on a free port against a small fixture repo."""

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from coderstanding.cli import build_parser
from coderstanding.layout import layout_path, read_layout
from coderstanding.apps import App
from coderstanding.notes import edge_key, node_key
from coderstanding.server import HOST, Api, make_server, view_args
from helpers import scan_fake

FILES = {
    "game.fk": "type Game class entry\nref composition Player field\nref association Player call move\n",
    "player.fk": "type Player class\nref composition Weapon field\n",
    "weapon.fk": "type Weapon class\n",
}


@pytest.fixture
def server(tmp_path):
    graph = scan_fake(tmp_path / "repo", FILES)
    app = Api(App("repo", tmp_path / "repo", tmp_path / "state"), graph=graph)
    srv = make_server(app, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://{HOST}:{srv.server_port}", app
    srv.shutdown()
    srv.server_close()


def get(base, path):
    with urlopen(base + path) as response:
        return json.loads(response.read())


def put(base, path, data, method="PUT"):
    request = Request(base + path, data=json.dumps(data).encode(), method=method,
                      headers={"Content-Type": "application/json"})
    with urlopen(request) as response:
        return json.loads(response.read())


def node_ids(view):
    return {n["id"] for n in view["nodes"]}


def test_fixture_levels(server):
    _, app = server
    levels = {n.id: n.level for n in app.graph.nodes.values()}
    assert levels["Game"] < levels["Player"] < levels["Weapon"]


def test_view_follows_level(server):
    base, app = server
    low = get(base, "/api/view?level=1")
    high = get(base, f"/api/view?level={app.meta()['max_level']}")
    assert node_ids(low) < node_ids(high)
    assert high["counts"]["total"] == len(app.graph.nodes)
    assert {"key", "from", "to", "direct", "kinds", "calls", "via"} <= set(high["edges"][0])


def test_view_args_parse_zoom_and_toggles():
    args = view_args({"level": ["2"], "view": ["concrete"], "scene": ["0"],
                      "zoom_from": ["A"], "zoom_to": ["B"], "zoom_level": ["2"],
                      "via": ["X", "Y"]})
    assert args["level"] == 2 and args["view"] == "concrete"
    assert args["show"] == {"scene": False, "external": True, "test": False}
    assert args["zoom"] == {"from": "A", "to": "B", "level": 2, "via": ["X", "Y"]}
    assert view_args({})["zoom"] is None


def test_meta(server):
    base, _ = server
    meta = get(base, "/api/meta")
    assert meta["max_level"] >= 2 and meta["domains"] is False
    assert sum(meta["levels"].values()) == 3


def test_node_details(server):
    base, _ = server
    node = get(base, "/api/node/" + quote("Game", safe=""))
    assert node["file"] == "game.fk" and node["line"] == 1
    assert [o["id"] for o in node["out"]] == ["Player"]
    assert {"members", "external_refs", "in"} <= set(node)


def test_unknown_node_and_route_are_404(server):
    base, _ = server
    for path in ("/api/node/Nope", "/api/nothing"):
        with pytest.raises(HTTPError) as error:
            get(base, path)
        assert error.value.code == 404


def test_bad_query_is_400(server):
    base, _ = server
    with pytest.raises(HTTPError) as error:
        get(base, "/api/view?level=x")
    assert error.value.code == 400


def test_stack(server):
    base, app = server
    assert get(base, "/api/stack") == app.graph.stack


def test_layout_round_trip(server, tmp_path):
    base, _ = server
    assert get(base, "/api/layout") == {"items": []}
    put(base, "/api/layout", {"items": [{"key": "Player", "x": 10, "y": 20, "pinned": True},
                                        {"key": "Game", "x": 1.234, "y": 2}]})
    result = put(base, "/api/layout", {"items": [{"key": "Game", "x": 5, "y": 6}],
                                       "remove": ["Nope"]})
    assert result["items"] == [{"key": "Game", "x": 5.0, "y": 6.0, "pinned": False},
                               {"key": "Player", "x": 10.0, "y": 20.0, "pinned": True}]
    lines = layout_path(tmp_path / "state").read_text().splitlines()
    assert lines[0] == "[" and lines[-1] == "]" and len(lines) == 4  # one line per class, sorted
    assert '"Game"' in lines[1] and '"Player"' in lines[2]
    put(base, "/api/layout", {"remove": ["Player"]})
    assert list(read_layout(tmp_path / "state")) == ["Game"]


def test_bad_layout_is_400(server):
    base, _ = server
    with pytest.raises(HTTPError) as error:
        put(base, "/api/layout", {"items": [{"x": 1, "y": 2}]})
    assert error.value.code == 400


def test_serves_viewer(server):
    base, _ = server
    with urlopen(base + "/") as response:
        assert b"vendor/cytoscape.min.js" in response.read()


def test_serve_cli_options():
    args = build_parser().parse_args(["serve", "mygame", "--port", "9000", "--data", "s"])
    assert (args.app, args.port, args.data) == ("mygame", 9000, "s")
    assert build_parser().parse_args(["serve", "r"]).port == 8517


def view_edge(base, from_id, to):
    view = get(base, "/api/view?level=3")
    return next(e for e in view["edges"] if (e["from"], e["to"]) == (from_id, to))


def view_node(base, node_id):
    return next(n for n in get(base, "/api/view?level=3")["nodes"] if n["id"] == node_id)


def test_put_notes_node_and_edge_show_in_view(server):
    base, _ = server
    note = put(base, "/api/notes", {"key": node_key("Game"),
                                    "fields": {"hint": "the loop", "description": "Line 1\nLine 2"}})
    assert note["hint"] == "the loop" and note["hint_by"] == "you"
    put(base, "/api/notes", {"key": edge_key("Game", "Player"), "fields": {"hint": "drives"}})
    game = view_node(base, "Game")
    assert (game["hint"], game["description"], game["description_by"]) == \
        ("the loop", "Line 1\nLine 2", "you")
    edge = view_edge(base, "Game", "Player")
    assert (edge["hint"], edge["hint_by"]) == ("drives", "you")


def test_put_notes_empty_string_clears(server, tmp_path):
    base, _ = server
    key = node_key("Game")
    put(base, "/api/notes", {"key": key, "fields": {"hint": "x", "description": "y"}})
    note = put(base, "/api/notes", {"key": key, "fields": {"hint": ""}})
    assert note["hint"] is None and note["description"] == "y"
    assert "hint" not in (tmp_path / "state" / "notes.jsonl").read_text()


def test_put_notes_bad_input_is_400(server):
    base, _ = server
    bad = [{"key": "garbage", "fields": {"hint": "x"}},
           {"key": "node:Nope", "fields": {"hint": "x"}},
           {"key": "edge:Game→Nope", "fields": {"hint": "x"}},
           {"key": "edge:Game", "fields": {"hint": "x"}},
           {"key": node_key("Game"), "fields": {"level": 2}},
           {"key": node_key("Game"), "fields": {"hint": 5}},
           {"key": node_key("Game")}]
    for body in bad:
        with pytest.raises(HTTPError) as error:
            put(base, "/api/notes", body)
        assert error.value.code == 400, body


def test_accept_turns_claude_draft_into_yours(server):
    base, app = server
    notes = app.app.load_notes()
    key = edge_key("Game", "Player")
    notes.set(key, "claude", {"hint": "draft", "description": "long draft"})
    app.app.save_notes(notes)
    assert view_edge(base, "Game", "Player")["hint_by"] == "claude"
    note = put(base, "/api/notes/accept", {"key": key, "fields": ["hint"]}, method="POST")
    assert (note["hint_by"], note["description_by"]) == ("you", "claude")
    assert view_edge(base, "Game", "Player")["hint_by"] == "you"


def test_accept_bad_key_is_400(server):
    base, _ = server
    with pytest.raises(HTTPError) as error:
        put(base, "/api/notes/accept", {"key": "x", "fields": ["hint"]}, method="POST")
    assert error.value.code == 400


def test_put_review_node_and_edge(server):
    base, _ = server
    note = put(base, "/api/notes", {"key": node_key("Game"), "fields": {"review": "too big"}})
    assert (note["review"], note["review_by"], note["review_ai"]) == ("too big", "you", False)
    assert view_node(base, "Game")["review"] == "too big"
    cleared = put(base, "/api/notes", {"key": node_key("Game"), "fields": {"review": ""}})
    assert cleared["review"] is None


def test_put_status_tags_rules(server):
    base, _ = server
    note = put(base, "/api/notes", {"key": node_key("Game"), "fields": {
        "status": "wip", "tags": "loop, core", "rules": "never block\nno globals"}})
    assert (note["status"], note["tags"], note["tags_by"]) == ("wip", ["loop", "core"], "you")
    game = view_node(base, "Game")
    assert (game["status"], game["tags"], game["rules"]) == ("wip", ["loop", "core"],
                                                             "never block\nno globals")
    assert get(base, "/api/meta")["tags"] == ["core", "loop"]
    cleared = put(base, "/api/notes", {"key": node_key("Game"), "fields": {"tags": "", "status": ""}})
    assert cleared["tags"] is None and cleared["status"] is None
    with pytest.raises(HTTPError) as error:
        put(base, "/api/notes", {"key": node_key("Game"), "fields": {"status": "shaky"}})
    assert error.value.code == 400


def test_meta_lists_statuses_and_warning_help(server):
    meta = get(server[0], "/api/meta")
    assert meta["statuses"] == ["stable", "wip", "fragile", "deprecated"]
    assert set(meta["warning_help"]) == {"unused", "god", "cycle", "unsure", "unresolved", "layer", "signal-unconnected", "signal-unemitted", "bad-handler", "autoload-unused"}


def test_view_test_param(tmp_path):
    files = {**FILES, "tests/test_game.fk": "type TestGame class\nref dependency Game call\n"}
    graph = scan_fake(tmp_path / "repo", files)
    api = Api(App("repo", tmp_path / "repo", tmp_path / "state"), graph=graph)
    hidden = api.view({"level": ["3"]})
    assert "TestGame" not in node_ids(hidden)
    assert next(n for n in hidden["nodes"] if n["id"] == "Game")["tested_by"] == ["tests/test_game.fk"]
    assert "TestGame" in node_ids(api.view({"level": ["5"], "test": ["1"]}))


def test_view_expand_param_and_text_route(server):
    base, _ = server
    view = get(base, "/api/view?level=3&expand=Game&expand=Nope")
    assert list(view["expanded"]) == ["Game"]
    assert any(l["from"] == "Game" for l in view["fanout"])
    assert view_args({"expand": ["A", ""]})["expand"] == ["A"]
    with urlopen(base + "/api/text?level=3&expand=Game&select=Player") as response:
        assert response.headers["Content-Type"].startswith("text/plain")
        lines = response.read().decode().splitlines()
    assert lines[0] == "$ coderstanding view repo --level 3 --expand Game --select Player"
    assert "Game (fields) > Player: [association, composition] move" in lines  # no from_member
    assert "selected Player:" in lines


def test_put_notes_ignore_warnings_and_text_via(server):
    base, _ = server
    note = put(base, "/api/notes", {"key": node_key("Game"), "fields": {"ignore_warnings": ["unused"]}})
    assert note["key"] == node_key("Game")
    assert view_node(base, "Game")["ignore_warnings"] == ["unused"]
    put(base, "/api/notes", {"key": node_key("Game"), "fields": {"ignore_warnings": []}})
    assert view_node(base, "Game")["ignore_warnings"] == []
    with urlopen(base + "/api/text?level=3&zoom_from=Game&zoom_to=Player&zoom_level=3"
                 "&via=Player") as response:
        assert "--via Player" in response.read().decode().splitlines()[0]
