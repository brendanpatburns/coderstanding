"""Comments (comments.py, DESIGN 7d): threads on tours and steps, line anchors, lost, merge,
authors, CLI and API."""

import json
import subprocess

import pytest

from coderstanding.apps import App, REGISTRY_ENV, rescan
from coderstanding.cli import main
from coderstanding.comments import Comments, sort_comments
from coderstanding.notes import Notes, human_author
from coderstanding.server import Api
from test_tours import CODE, TOUR, setup, tours_of
from test_tours_api import call, serve
from helpers import write_files


@pytest.fixture
def repo(tmp_path):
    graph = setup(tmp_path)
    write_files(tmp_path, {".coderstanding/tours/walk.md": TOUR})
    return tmp_path, tours_of(tmp_path, graph)


def file_of(root):
    return root / ".coderstanding/tours/walk.comments.jsonl"


def test_threads_replies_resolve_and_counts(repo):
    root, tours = repo
    store = tours.comments
    first = store.add("tour:walk#k1", "Rename hp?", "ana")
    reply = store.add(None, "Done.", "claude", ai=True, parent=first["id"])
    other = store.add("tour:walk", "Nice tour", "bob")
    assert reply["target"] == "tour:walk#k1" and reply["ai"] is True
    shown = store.list_json("tour:walk")
    assert [c["id"] for c in shown] == [other["id"], first["id"], reply["id"]]  # sorted by thread
    assert shown[2]["thread"] == first["id"] and shown[2]["parent"] == first["id"]
    assert store.open_counts("tour:walk") == {"tour:walk#k1": 1, "tour:walk": 1}
    store.resolve(reply["id"], "ana")  # a reply resolves its whole thread
    assert store.list_json("tour:walk")[1]["resolved"]["by"] == "ana"
    assert [c["id"] for c in store.list_json("tour:walk", resolved=False)] == [other["id"]]
    assert tours.tour_json(tours.get("walk"))["steps"][0]["comments"] == 0
    store.resolve(first["id"], "ana", done=False)
    data = tours.tour_json(tours.get("walk"))
    assert (data["comments"], data["steps"][0]["comments"]) == (2, 1)
    assert tours.list_json()["tours"][0]["comments"] == 2
    assert store.list_json("tour:wal") == []  # a prefix stops at the id


def test_edit_delete_own_only(repo):
    _, tours = repo
    store = tours.comments
    c = store.add("tour:walk#k1", "typo", "ana")
    with pytest.raises(PermissionError):
        store.edit(c["id"], "hijack", "bob")
    with pytest.raises(PermissionError):
        store.delete(c["id"], "bob")
    assert store.edit(c["id"], "fixed", "ana")["edited"]
    reply = store.add(None, "ok", "bob", parent=c["id"])
    store.delete(c["id"], "ana")
    shown = store.list_json("tour:walk")
    assert shown[0]["deleted"] and shown[0]["text"] == "" and len(shown) == 2  # kept for its reply
    store.delete(reply["id"], "bob")
    assert store.list_json("tour:walk") == []
    with pytest.raises(ValueError):
        store.add("tour:walk", "  ", "ana")
    with pytest.raises(ValueError):
        store.add("spec:x", "hi", "ana")  # not a kind of this store


def test_storage_sorted_one_per_line_with_gitattributes(repo):
    root, tours = repo
    store = tours.comments
    a = store.add("tour:walk#k2", "second step", "ana")
    b = store.add("tour:walk#k1", "first step", "bob")
    store.add(None, "reply", "ana", parent=a["id"])
    rows = [json.loads(line) for line in file_of(root).read_text().splitlines()]
    assert [r["target"] for r in rows] == ["tour:walk#k1", "tour:walk#k2", "tour:walk#k2"]
    assert rows[0]["id"] == b["id"] and rows[2]["parent"] == a["id"]
    assert (file_of(root).parent / ".gitattributes").read_text() == "*.comments.jsonl merge=union\n"
    assert "ai" not in rows[0]  # written only when true


def test_two_writers_merge_by_concatenation(repo, tmp_path):
    """Two people's files after git's union merge: the shared base twice, each one's new lines,
    a comment changed on one side; reading keeps one copy of each, the newest."""
    root, tours = repo
    store = tours.comments
    base = store.add("tour:walk#k1", "base", "ana")
    base_text = file_of(root).read_text()
    store.add("tour:walk#k1", "ana's", "ana")
    mine = file_of(root).read_text()
    file_of(root).write_text(base_text)
    store.add("tour:walk#k2", "bob's", "bob")
    resolved = store.resolve(base["id"], "bob")
    resolved["changed"] = "2999-01-01T00:00:00Z"  # bob's change is newer than ana's copy
    rows = [json.loads(line) for line in file_of(root).read_text().splitlines()]
    rows = [resolved if r["id"] == base["id"] else r for r in rows]
    theirs = "".join(json.dumps(r) + "\n" for r in rows)
    file_of(root).write_text(mine + theirs)
    shown = store.list_json("tour:walk")
    assert sorted(c["text"] for c in shown) == ["ana's", "base", "bob's"]
    assert next(c for c in shown if c["id"] == base["id"])["resolved"]["by"] == "bob"
    store.add("tour:walk", "after the merge", "ana")  # a write cleans the file up
    assert len(file_of(root).read_text().splitlines()) == 4


def test_sort_order_is_thread_then_time():
    rows = [{"id": "r", "target": "tour:t#a", "created": "3", "parent": "x"},
            {"id": "y", "target": "tour:t#a", "created": "2"},
            {"id": "x", "target": "tour:t#a", "created": "1"},
            {"id": "z", "target": "tour:t", "created": "9"}]
    assert [c["id"] for c in sort_comments(rows)] == ["z", "x", "r", "y"]


def test_line_comment_follows_an_edit_above(repo):
    root, tours = repo
    first = tours.comments.add("tour:walk#k1", "this line", "ana", line=3)  # `self.hp -= 1`
    assert first["anchor"] == {"at": "Player#hit", "off": 1}
    shown = tours.comments.list_json("tour:walk")[0]
    assert (shown["path"], shown["line"]) == ("m.py", 3)
    graph = setup(root, "# header\n\n" + CODE)  # two lines above the class
    moved = tours_of(root, graph).comments.list_json("tour:walk")[0]
    assert moved["line"] == 5
    with pytest.raises(ValueError):
        tours.comments.add("tour:walk", "no code", "ana", line=3)  # the tour itself has no anchor
    with pytest.raises(ValueError):
        tours.comments.add("tour:walk#k1", "above", "ana", line=1)


def test_comments_on_a_deleted_step_are_lost_not_dropped(repo):
    root, tours = repo
    tours.comments.add("tour:walk#k2", "about the world", "ana")
    tour = tours.get("walk")
    tour.steps = tour.steps[:1]
    tours.save(tour)
    shown = tours.comments.list_json("tour:walk")
    assert len(shown) == 1 and shown[0]["lost"]
    assert tours.comments.lost("tour:") == {"tour:walk#k2": 1}


def test_human_author_from_git_config(tmp_path, monkeypatch):
    monkeypatch.delenv("CODERSTANDING_USER")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Ana Lima"], check=True)
    assert human_author(tmp_path) == "Ana Lima"
    monkeypatch.setenv("CODERSTANDING_USER", "Bob")
    assert human_author(tmp_path) == "Bob"


def test_a_human_edit_replaces_another_humans_value():
    notes = Notes()
    notes.set("node:A", "you", {"hint": "old"})  # an old viewer write
    notes.set("node:A", "Ana Lima", {"hint": "new"}, ai=False)
    assert notes.get("node:A", "hint") == ("new", "Ana Lima")


def test_cli_comment_commands(repo, monkeypatch, capsys, tmp_path):
    root, _ = repo
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "reg.json"))
    (tmp_path / "reg.json").write_text(json.dumps({"app": {"path": str(root)}}))
    main(["rescan", "app"])
    capsys.readouterr()
    main(["comment", "app", "tour:walk#1", "Explain hp", "--line", "3", "--by", "ana"])
    out = capsys.readouterr().out
    assert out.startswith("added [") and "on tour:walk#k1 line 3 as ana" in out
    first = out.split("[")[1].split("]")[0]
    main(["comment", "app", "--reply", first, "Added a sentence."])
    assert "as claude (ai)" in capsys.readouterr().out
    main(["tour", "app", "walk", "--no-code"])
    assert "1. Hits {Player#hit} (1 open comment)" in capsys.readouterr().out
    main(["tour", "app", "walk", "--comments"])
    text = capsys.readouterr().out.splitlines()
    assert text[0] == "app tour walk comments: 1 open"
    assert text[1] == f"[{first}] step 1 Hits m.py:3 ana: Explain hp"
    assert text[2].startswith("  [") and text[2].endswith("claude (ai): Added a sentence.")
    main(["comment", "app", "--resolve", first])
    assert "resolved the thread" in capsys.readouterr().out
    main(["tour", "app", "walk", "--comments"])
    assert capsys.readouterr().out.strip() == "app tour walk comments: 0 open, 1 resolved (--all lists them)"
    main(["tour", "app", "walk", "--comments", "--all"])
    assert "(resolved by claude)" in capsys.readouterr().out
    main(["comment", "app", "--reopen", first])
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["comment", "app", "tour:walk#9", "no such step"])  # no step 9, no key 9
    with pytest.raises(SystemExit):
        main(["comment", "app", "tour:walk"])  # no text


@pytest.fixture
def desk(tmp_path):
    write_files(tmp_path / "repo", {"m.py": CODE, ".coderstanding/tours/walk.md": TOUR})
    app = App("app", tmp_path / "repo", tmp_path / "repo" / ".coderstanding")
    srv, base = serve(Api(app, graph=rescan(app)))
    yield base
    srv.shutdown()
    srv.server_close()


def test_api_comments(desk):
    base = desk
    made = call(base, "/api/comments?app=app", {"target": "tour:walk#k1", "text": "Line?", "line": 4}, "POST")
    assert made["me"] == "you" and made["comment"]["line"] == 4 and made["comment"]["by"] == "you"
    cid = made["comment"]["id"]
    reply = call(base, "/api/comments?app=app", {"parent": cid, "text": "Yes"}, "POST")
    assert len(reply["comments"]) == 2
    listed = call(base, "/api/comments?app=app&target=tour:walk")
    assert [c["id"] for c in listed["comments"]][0] == cid
    assert call(base, "/api/comments?app=app&target=tour:walk%23k2")["comments"] == []
    edited = call(base, f"/api/comments/{cid}?app=app", {"text": "Line 4?"}, "PATCH")
    assert edited["comment"]["text"] == "Line 4?" and edited["comment"]["edited"]
    done = call(base, f"/api/comments/{cid}?app=app", {"resolved": True}, "PATCH")
    assert done["comment"]["resolved"]["by"] == "you"
    assert call(base, "/api/tours/walk?app=app")["comments"] == 0
    assert call(base, f"/api/comments/{cid}?app=app", {}, "PATCH")["status"] == 400
    assert call(base, "/api/comments/beef?app=app", {"text": "x"}, "PATCH")["status"] == 400
    assert call(base, "/api/comments?app=app", {"target": "tour:walk", "text": ""}, "POST")["status"] == 400
    assert call(base, "/api/comments?app=app", b"x", "POST", "text/plain")["status"] == 415
    gone = call(base, f"/api/comments/{reply['comment']['id']}?app=app", None, "DELETE", None)
    assert len(gone["comments"]) == 1


def test_api_comment_guards(desk, tmp_path):
    base = desk
    made = call(base, "/api/comments?app=app", {"target": "tour:walk", "text": "mine"}, "POST")
    cid = made["comment"]["id"]
    # someone else's comment (written by the CLI as the AI) cannot be edited or deleted here
    store = Comments(tmp_path / "repo/.coderstanding")
    store.add_kind("tour", "tours", type("Owner", (), {"comment_exists": lambda s, r: True,
                                                         "comment_anchor": lambda s, r: None})())
    theirs = store.add("tour:walk", "AI note", "claude", ai=True)
    assert call(base, f"/api/comments/{theirs['id']}?app=app", {"text": "x"}, "PATCH")["status"] == 403
    assert call(base, f"/api/comments/{theirs['id']}?app=app", None, "DELETE", None)["status"] == 403
    assert call(base, f"/api/comments/{theirs['id']}?app=app", {"resolved": True}, "PATCH")["comment"]["resolved"]
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    request = Request(base + f"/api/comments/{cid}?app=app", data=b'{"text":"x"}', method="PATCH",
                      headers={"Content-Type": "application/json", "Origin": "https://evil.example"})
    with pytest.raises(HTTPError) as error:
        urlopen(request)
    assert error.value.code == 403
