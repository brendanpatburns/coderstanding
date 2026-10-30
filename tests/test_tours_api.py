"""Tour routes of the server (DESIGN 7c): JSON, Markdown, writes, audio."""

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from coderstanding.apps import App, rescan
from coderstanding.server import HOST, Api, make_server
from coderstanding.tours import MAX_AUDIO_BYTES, open_tours
from coderstanding.textout import format_tour
from test_tours import CODE, TOUR
from helpers import write_files


def serve(api_or_hub):
    srv = make_server(api_or_hub, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://{HOST}:{srv.server_port}"


def call(base, path, data=None, method="GET", content_type="application/json", raw=False):
    body = data if isinstance(data, bytes) or data is None else json.dumps(data).encode()
    request = Request(base + path, data=body, method=method,
                      headers={"Content-Type": content_type} if content_type else {})
    try:
        with urlopen(request) as response:
            payload = response.read()
            return payload if raw else json.loads(payload)
    except HTTPError as error:
        return {"status": error.code, **json.loads(error.read())}


@pytest.fixture
def desk(tmp_path):
    """A desktop app with one stamped tour, served locally."""
    write_files(tmp_path / "repo", {"m.py": CODE, ".coderstanding/tours/walk.md": TOUR})
    app = App("app", tmp_path / "repo", tmp_path / "repo" / ".coderstanding")
    graph = rescan(app)
    tours = open_tours(app, graph, app.load_notes())
    tour = tours.get("walk")
    for step in tour.steps:
        tours.stamp(step)
    tours.save(tour)
    srv, base = serve(Api(app, graph=graph))
    yield base, app
    srv.shutdown()
    srv.server_close()


def test_list_get_and_markdown(desk):
    base, _ = desk
    assert call(base, "/api/tours?app=app")["tours"][0] == {
        "id": "walk", "title": "A walk", "by": "you", "ai": False, "steps": 2, "changed": 0,
        "ai_steps": 1, "comments": 0}
    tour = call(base, "/api/tours/walk?app=app")
    assert tour["steps"][0]["code"]["lines"] == ["        self.hp -= 1", "        self.flash()"]
    md = call(base, "/api/tours/walk.md?app=app", raw=True).decode()
    assert md.startswith("# A walk")
    assert call(base, "/api/tours/nope?app=app")["status"] == 404


def test_code_first_gives_the_stored_line_offsets(desk):
    base, _ = desk
    for step, stored in zip(call(base, "/api/tours/walk?app=app")["steps"], ["+1..+2", "+0..+3"]):
        code = step["code"]
        assert f"+{code['from'] - code['first']}..+{code['to'] - code['first']}" == stored


def test_step_text_route_is_the_cli_text(desk):
    base, _ = desk
    tour = call(base, "/api/tours/walk?app=app")
    assert call(base, "/api/tours/walk.txt?app=app&step=2", raw=True).decode() == format_tour("app", tour, 2)
    assert call(base, "/api/tours/walk.txt?app=app", raw=True).decode() == format_tour("app", tour)
    assert call(base, "/api/tours/walk.txt?app=app&step=9")["status"] == 400


def test_view_takes_focus(desk):
    base, _ = desk
    view = call(base, "/api/view?app=app&level=5&focus=m.Player%23hit")
    assert view["focus"]["functions"] == ["m.Player#hit"] and "m.Player" in view["expanded"]


def test_put_still_true_accept(desk):
    base, _ = desk
    tour = call(base, "/api/tours/walk?app=app")
    lines = {"k1": "+1..+2", "k2": "+0..+3"}  # as stored in TOUR
    body = {"title": "A walk", "steps": [
        {**{k: s[k] for k in ("key", "title", "text", "targets")},
         "code": {"at": s["code"]["at"], "lines": lines[s["key"]], "mark": s["code"]["mark"]}}
        for s in tour["steps"]]}
    body["steps"][1]["text"] = "Edited by hand."
    saved = call(base, "/api/tours/walk?app=app", body, "PUT")
    assert [s["key"] for s in saved["steps"]] == ["k1", "k2"]
    assert saved["steps"][0] == tour["steps"][0]  # sent back unchanged: kept as it was
    assert (saved["steps"][1]["by"], saved["steps"][1]["ai"]) == ("you", False)
    assert call(base, "/api/tours/walk/still-true?app=app", {}, "POST")["id"] == "walk"
    assert call(base, "/api/tours/walk/accept?app=app", {"keys": ["k2"]}, "POST")["steps"][1]["ai"] is False
    assert call(base, "/api/tours/walk?app=app", {"title": 3}, "PUT")["status"] == 400
    assert call(base, "/api/tours/walk?app=app", body, "PUT", "text/plain")["status"] == 415


def test_audio_upload_serve_limits_delete(desk):
    base, app = desk
    up = call(base, "/api/tours/walk/audio/k1?app=app&dur=2.5", b"OggS-data", "PUT", "audio/ogg")
    rec = up["steps"][0]["audio"]["rec"]
    assert rec["dur"] == 2.5 and rec["url"] == "/api/tours/walk/audio/k1?app=app"
    assert call(base, rec["url"], raw=True) == b"OggS-data"
    assert (app.data / "tours/walk/k1.rec.ogg").exists()
    big = b"x" * (MAX_AUDIO_BYTES + 1)
    assert call(base, "/api/tours/walk/audio/k1?app=app", big, "PUT", "audio/webm")["status"] == 413
    assert call(base, "/api/tours/walk/audio/k1?app=app", b"x", "PUT", "audio/mpeg")["status"] == 415
    assert call(base, "/api/tours/walk/audio/k1?app=app", {"a": 1}, "PUT")["status"] == 415
    assert call(base, "/api/tours/walk/audio/k9?app=app", b"x", "PUT", "audio/ogg")["status"] == 400
    gone = call(base, "/api/tours/walk/audio/k1?app=app", None, "DELETE", None)
    assert gone["steps"][0]["audio"]["rec"] is None
    assert call(base, rec["url"])["status"] == 404


def test_cross_site_audio_upload_is_refused(desk):
    base, _ = desk
    request = Request(base + "/api/tours/walk/audio/k1?app=app", data=b"x", method="PUT",
                      headers={"Content-Type": "audio/ogg", "Origin": "https://evil.example"})
    with pytest.raises(HTTPError) as error:
        urlopen(request)
    assert error.value.code == 403
