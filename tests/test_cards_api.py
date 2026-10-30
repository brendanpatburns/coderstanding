"""Spec and memo routes of the server (DESIGN 7, 7d): list/fold/search/filter, get, create,
update, delete, accept, reverse links, view badges, guards."""

from urllib.request import Request, urlopen

import pytest

from coderstanding.apps import App, rescan
from coderstanding.server import Api
from helpers import write_files
from test_cards import MEMOS, SPECS
from test_tours import CODE
from test_tours_api import call, serve


@pytest.fixture
def desk(tmp_path):
    write_files(tmp_path / "repo", {"m.py": CODE,
                                    **{f".coderstanding/specs/{k}": v for k, v in SPECS.items()},
                                    **{f".coderstanding/memos/{k}": v for k, v in MEMOS.items()}})
    app = App("app", tmp_path / "repo", tmp_path / "repo" / ".coderstanding")
    srv, base = serve(Api(app, graph=rescan(app)))
    yield base, app
    srv.shutdown()
    srv.server_close()


def test_list_fold_zoom_search_filter(desk):
    base, _ = desk
    data = call(base, "/api/specs?app=app&level=1")
    assert [b["id"] for b in data["tree"]] == ["game", "tools"] and data["tree"][0]["finer"] == 2
    assert data["roots"] == ["game", "tools"] and data["max_level"] == 4 and data["hits"] is None
    assert data["stages"] == ["idea", "todo", "in-progress", "review", "implemented", "dropped"]
    assert data["priorities"] == ["high", "medium", "low"]
    assert data["problems"] == [{"id": "hits", "problem": "unknown link: Gone"}]
    hits = data["items"]["hits"]
    assert (hits["level"], hits["stage"], hits["children"], hits["unknown_links"], hits["focus"]) == \
        (2, "idea", ["heal"], ["Gone"], ["m.Player#hit", "m.World→m.Player"])
    assert call(base, "/api/specs?app=app&zoom=hits")["crumbs"][-1]["id"] == "hits"
    assert [h["path"] for h in call(base, "/api/specs?app=app&q=healing")["hits"]] == [
        "The game › Hits, damage and flashes › Healing"]
    assert [h["id"] for h in call(base, "/api/specs?app=app&stage=idea&sort=priority")["hits"]] == ["hits", "heal"]
    assert call(base, "/api/specs?app=app&stage=nope")["status"] == 400
    memos = call(base, "/api/memos?app=app&sort=importance")
    assert memos["kind"] == "memo" and [h["id"] for h in memos["hits"]] == ["why", "fix"]
    assert memos["stage_labels"] == {"todo": "todo", "in-progress": "in progress", "done": "done", "na": "N/A"}
    assert call(base, "/api/specs/nope?app=app")["status"] == 404
    assert call(base, "/api/specs?app=app&zoom=nope")["status"] == 400
    assert call(base, "/api/find?app=app&q=play")["classes"][0]["id"] == "m.Player"


def test_links_and_badges(desk):
    base, _ = desk
    assert [(h["kind"], h["id"]) for h in call(base, "/api/linked?app=app&at=m.World%E2%86%92m.Player")["items"]] == \
        [("spec", "hits"), ("memo", "why")]
    assert [h["id"] for h in call(base, "/api/linked?app=app&at=m.World")["items"]] == ["game"]
    view = call(base, "/api/view?app=app&level=9&badges=1")
    assert {n["id"]: n["cards"] for n in view["nodes"] if n["cards"]} == {"m.Player": 4, "m.World": 1}
    assert {e["key"]: e["cards"] for e in view["edges"]} == {"m.World→m.Player": 2}
    assert "cards" not in call(base, "/api/view?app=app&level=9")["nodes"][0]
    view = call(base, "/api/view?app=app&level=9&focus=m.World%E2%86%92m.Player")
    assert view["focus"]["edges"] == ["m.World→m.Player"]  # a linked line lights up


def test_create_update_move_accept_delete(desk):
    base, app = desk
    made = call(base, "/api/specs?app=app", {"title": "Save games", "parent": "game"}, "POST")
    assert made["id"] == "save-games" and made["by"] == "you" and made["level"] == 2 and made["stage"] == "idea"
    put = call(base, "/api/specs/save-games?app=app",
               {"title": "Saving", "stage": "review", "priority": "high", "importance": "low",
                "level": 3, "links": ["m.World", "m.World>m.Player"]}, "PUT")
    assert (put["title"], put["stage"], put["priority"], put["importance"], put["links"]) == \
        ("Saving", "review", "high", "low", ["World", "m.World>m.Player"])
    assert put["focus"] == ["m.World", "m.World→m.Player"]
    assert call(base, "/api/specs/save-games?app=app", {"position": 0}, "PUT")["order"] == 0
    assert call(base, "/api/specs/game?app=app")["children"] == ["save-games", "hits"]
    memo = call(base, "/api/memos?app=app", {"title": "A thought"}, "POST")
    assert (memo["kind"], memo["key"], memo["stage"]) == ("memo", "memo:a-thought", "na")
    assert (app.data / "memos" / "a-thought.md").exists()
    assert call(base, "/api/memos/a-thought?app=app", {"stage": "done"}, "PUT")["stage"] == "done"
    assert call(base, "/api/specs/heal/accept?app=app", {}, "POST")["ai"] is False
    gone = call(base, "/api/specs/hits?app=app", method="DELETE", content_type=None)
    assert gone == {"deleted": "hits", "moved": ["heal"]}
    assert call(base, "/api/memos/a-thought?app=app", method="DELETE", content_type=None)["moved"] == []


def test_refusals_and_guards(desk):
    base, app = desk
    bad = [({"parent": "heal"}, "cycle"), ({"level": 1}, "shallower"), ({"stage": "maybe"}, "stage must be"),
           ({"priority": "P1"}, "priority must be"), ({"colour": "red"}, "unknown fields"),
           ({"position": "1"}, "position must be")]
    for body, words in bad:
        answer = call(base, "/api/specs/hits?app=app", body, "PUT")
        assert answer["status"] == 400 and words in answer["error"], (body, answer)
    assert call(base, "/api/memos/why?app=app", {"stage": "implemented"}, "PUT")["status"] == 400
    assert call(base, "/api/specs?app=app", {"stage": "idea"}, "POST")["status"] == 400
    before = (app.data / "specs" / "tools.md").read_text()
    assert call(base, "/api/specs/tools?app=app", b"title=x", "PUT", "application/x-www-form-urlencoded")["status"] == 415
    for method, data in (("PUT", b'{"title": "x"}'), ("DELETE", None)):
        request = Request(base + "/api/specs/tools?app=app", data=data, method=method,
                          headers={"Content-Type": "application/json", "Origin": "https://evil.example"})
        with pytest.raises(Exception) as error:
            urlopen(request)
        assert error.value.code == 403
    assert (app.data / "specs" / "tools.md").read_text() == before
