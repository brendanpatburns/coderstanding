"""Tours (tours.py, DESIGN 7c): store, anchors, stale marks, authors, audio, focus, CLI text."""

import json

import pytest

from coderstanding.apps import App, REGISTRY_ENV
from coderstanding.cli import main
from coderstanding.fingerprints import SourceLines
from coderstanding.notes import Notes
from coderstanding.scan import scan_repo
from coderstanding.textout import format_tour, tour_markdown, tour_warning_lines
from coderstanding.tours import MAX_AUDIO_BYTES, Tours, open_tours
from coderstanding.views import compute_view
from helpers import write_files

CODE = """class Player:
    def hit(self):
        self.hp -= 1
        self.flash()

    def heal(self):
        self.hp += 1


class World:
    def run(self):
        p = Player()
        p.hit()
"""

TOUR = """---
id: walk
title: A walk
by: you
steps:
  - key: k1
    targets: [Player#hit]
    code: {at: Player#hit, lines: +1..+2, mark: [+2]}
  - key: k2
    targets: [World>Player, World]
    code: {at: World, lines: +0..+3}
    by: claude
    ai: true
---
## Hits
[[Player#hit]] loses health.

## The world
[[World]] makes a player.
"""


def setup(root, code=CODE):
    write_files(root, {"m.py": code})
    return scan_repo(root, use_cache=False)


def tours_of(root, graph):
    return Tours(root / ".coderstanding", graph, Notes(), SourceLines(root))


@pytest.fixture
def repo(tmp_path):
    graph = setup(tmp_path)
    write_files(tmp_path, {".coderstanding/tours/walk.md": TOUR})
    tours = tours_of(tmp_path, graph)
    tour = tours.get("walk")
    for step in tour.steps:
        tours.stamp(step)
    tours.save(tour)
    return tmp_path, tours


def test_store_round_trip_keeps_keys_authors_and_text(repo):
    root, tours = repo
    tour = tours.get("walk")
    assert [s.key for s in tour.steps] == ["k1", "k2"]
    assert [(s.by, s.ai) for s in tour.steps] == [("you", False), ("claude", True)]
    assert tour.steps[0].code.fp and tour.steps[0].code.was == "3-4"
    text = (root / ".coderstanding/tours/walk.md").read_text()
    assert "## Hits\n[[Player#hit]] loses health." in text
    tours.save(tour)
    assert (root / ".coderstanding/tours/walk.md").read_text() == text


def test_tour_json_contract(repo):
    _, tours = repo
    data = tours.tour_json(tours.get("walk"), "app")
    step = data["steps"][0]
    assert set(data) >= {"id", "title", "by", "ai", "steps"}
    assert set(step) >= {"key", "title", "text", "targets", "by", "ai", "stale", "lost", "code",
                         "audio"}
    assert step["code"] == {"at": "Player#hit", "path": "m.py", "first": 2, "from": 3, "to": 4, "mark": [4],
                            "lines": ["        self.hp -= 1", "        self.flash()"], "was": None}
    assert step["audio"] == {"rec": None} and not step["stale"] and not step["lost"]
    assert step["focus"] == ["m.Player#hit"] and step["expand"] == ["m.Player"]
    assert tours.list_json()["tours"] == [{"id": "walk", "title": "A walk", "by": "you",
                                           "ai": False, "steps": 2, "changed": 0, "ai_steps": 1}]


def test_anchor_follows_an_edit_above_it(repo):
    root, _ = repo
    graph = setup(root, "import os\n\n\n" + CODE)
    step = tours_of(root, graph).tour_json(tours_of(root, graph).get("walk"))["steps"][0]
    assert (step["code"]["from"], step["code"]["mark"]) == (6, [7])
    assert step["code"]["lines"][1].strip() == "self.flash()" and not step["stale"]


def test_stale_then_still_true(repo):
    root, _ = repo
    graph = setup(root, CODE.replace("self.hp -= 1", "self.hp -= 2"))
    tours = tours_of(root, graph)
    step = tours.tour_json(tours.get("walk"))["steps"][0]
    assert step["stale"] and step["code"]["was"] == "3-4"
    assert tours.stale_steps() == [("walk", 1)]
    assert tour_warning_lines(tours.stale_steps(), []) == [
        "stale tour steps (code changed since written): walk 1"]
    _, done = tours.still_true("walk")
    assert [s.key for s in done] == ["k1"] and tours.stale_steps() == []


def test_lost_anchor_and_targets(repo):
    root, _ = repo
    graph = setup(root, CODE.replace("def hit(self)", "def punch(self)"))
    tours = tours_of(root, graph)
    step = tours.tour_json(tours.get("walk"))["steps"][0]
    assert step["lost"] and step["lost_targets"] == ["Player#hit"]
    assert step["code"]["from"] is None and step["code"]["lines"] == []
    assert tours.lost_steps() == [("walk", 1)]


def test_put_keeps_keys_restamps_changed_steps_and_makes_them_yours(repo):
    _, tours = repo
    before = tours.get("walk")
    body = {"title": "A walk", "steps": [
        {"key": "k2", "title": "The world", "text": "Edited.", "targets": ["World"],
         "code": {"at": "World", "lines": "+0..+3", "mark": [12]}},
        {"key": "k1", "title": "Hits", "text": "[[Player#hit]] loses health.",
         "targets": ["Player#hit"], "code": {"at": "Player#hit", "lines": "+1..+2", "mark": [4]}},
        {"title": "New", "text": "x", "targets": ["Player"],
         "code": {"at": "Player", "lines": "+0..+1", "mark": []}}]}
    tour = tours.put("walk", body)
    assert [s.key for s in tour.steps][:2] == ["k2", "k1"] and len(tour.steps[2].key) == 6
    assert tour.steps[1] == before.steps[0]  # unchanged: kept as is
    assert (tour.steps[0].by, tour.steps[0].ai, tour.steps[0].code.mark) == ("you", False, [2])
    with pytest.raises(ValueError):
        tours.put("walk", {"title": "", "steps": []})


def test_accept_turns_ai_steps_into_yours(repo):
    _, tours = repo
    tour = tours.accept("walk")
    assert all((s.by, s.ai) == ("you", False) for s in tour.steps)


def test_draft_and_step_edit(repo):
    _, tours = repo
    tour = tours.draft("drafted", "Drafted", ["World>Player", "Player#hit"])
    assert tour.ai and all(s.ai and s.by == "claude" for s in tour.steps)
    first = tours.tour_json(tour)["steps"][0]
    assert first["code"]["at"] == "World#run" and first["code"]["mark"] == [12]
    with pytest.raises(ValueError):
        tours.draft("drafted", "Again", ["Player"])
    tour, step = tours.edit_step("walk", 1, "you", False, text="Better words.")
    assert (step.text, step.by, step.ai) == ("Better words.", "you", False)


def test_audio_set_stale_delete_and_orphans(repo):
    root, tours = repo
    tour = tours.set_audio("walk", "k1", b"OggS..", "audio/ogg", 3.5)
    rec = tours.tour_json(tour, "app")["steps"][0]["audio"]["rec"]
    assert rec == {"by": "you", "dur": 3.5, "stale": False, "url": "/api/tours/walk/audio/k1?app=app"}
    assert tours.audio_file("walk", "k1")[1] == "audio/ogg"
    tours.edit_step("walk", 1, text="New words.")
    assert tours.tour_json(tours.get("walk"))["steps"][0]["audio"]["rec"]["stale"]
    with pytest.raises(ValueError):
        tours.set_audio("walk", "k1", b"x" * (MAX_AUDIO_BYTES + 1), "audio/webm")
    with pytest.raises(ValueError):
        tours.set_audio("walk", "k1", b"x", "audio/mpeg")
    (root / ".coderstanding/tours/walk/zz.rec.webm").write_bytes(b"x")
    assert tours.orphan_audio() == ["walk/zz.rec.webm"]
    tours.delete_audio("walk", "k1")
    assert not (root / ".coderstanding/tours/walk/k1.rec.ogg").exists()


def test_deleting_a_step_deletes_its_recording(repo):
    root, tours = repo
    tours.set_audio("walk", "k1", b"webm", "audio/webm")
    tour = tours.get("walk")
    tour.steps = tour.steps[1:]
    tours.save(tour)
    assert not (root / ".coderstanding/tours/walk/k1.rec.webm").exists()


def test_focus_view_function_and_connection(repo):
    _, tours = repo
    view = compute_view(tours.graph, level=5, focus=["m.Player#hit", "m.World→m.Player"])
    assert view["focus"]["functions"] == ["m.Player#hit"]
    assert "m.World→m.Player" in view["focus"]["edges"]
    assert "m.Player" in view["expanded"]
    assert compute_view(tours.graph, level=5, focus=["Nope"])["focus"] is None


def test_lean_text_marked_and_markdown(repo):
    _, tours = repo
    data = tours.tour_json(tours.get("walk"))
    text = format_tour("app", data, 1)
    assert text.splitlines()[0].startswith("$ coderstanding view app --level")
    assert "--focus 'Player#hit' --expand m.Player" in text.splitlines()[0]
    assert text.splitlines()[1:] == ["1/2 Hits {Player#hit}", "[[Player#hit]] loses health.",
                                     "m.py:3-4", "    3         self.hp -= 1",
                                     ">   4         self.flash()"]
    assert format_tour("app", data, 1, marked=True).splitlines()[-1] == "m.py:4         self.flash()"
    assert "self.hp" not in format_tour("app", data, code=False)
    md = tour_markdown("app", data)
    assert md.startswith("# A walk\n\nBy you · 2 steps")
    assert "## 1. Hits\n\n`Player#hit` loses health.\n\n`m.py:3-4`\n\n```\n" in md
    assert ">    4          self.flash()" in md


def test_cli_tour_commands(repo, monkeypatch, capsys, tmp_path):
    root, _ = repo
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "reg.json"))
    (tmp_path / "reg.json").write_text(json.dumps({"app": {"path": str(root)}}))
    main(["tours", "app"])
    assert capsys.readouterr().out.strip() == "walk A walk 2 steps [you] 1 ai"
    main(["tour", "app", "walk", "--step", "2", "--audio"])
    assert "2/2 The world {World>Player, World} audio: voice" in capsys.readouterr().out
    main(["tour", "app", "walk", "--md", "-o", str(tmp_path / "walk.md")])
    capsys.readouterr()
    assert (tmp_path / "walk.md").read_text().startswith("# A walk")
    main(["tour", "app", "new", "--draft", "New one", "Player#heal"])
    assert "drafted new: 1 steps by claude (AI draft)" in capsys.readouterr().out
    main(["tour", "app", "new", "--step", "1", "--text", "Heals."])
    assert open_tours(App("app", root, root / ".coderstanding"), None, None).store.exists("new")
    assert "## Player#heal\nHeals." in (root / ".coderstanding/tours/new.md").read_text()
    main(["tour", "app", "walk", "--still-true"])
    assert "walk: still true (0 steps)" in capsys.readouterr().out
