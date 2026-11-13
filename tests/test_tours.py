"""Tours (tours.py, DESIGN 7c): store, anchors, stale marks, authors, audio, focus, CLI text."""

import json

import pytest

from coderstanding.apps import App, REGISTRY_ENV
from coderstanding.cli import main
from coderstanding.fingerprints import SourceLines
from coderstanding.notes import Notes, node_key
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
                                           "ai": False, "start": False, "steps": 2, "changed": 0, "ai_steps": 1, "comments": 0}]


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
    main(["rescan", "app"])
    capsys.readouterr()
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


def test_line_notes_round_trip_json_and_text(repo):
    root, tours = repo
    tour, step = tours.edit_note("walk", 1, "4", "Shows the hit.", cue="loses health")
    assert (step.by, step.ai) == ("claude", True)
    assert "notes: [{lines: +2..+2, text: Shows the hit., cue: loses health}]" in \
        (root / ".coderstanding/tours/walk.md").read_text()
    again = tours.get("walk").steps[0]
    assert again.notes == step.notes and again.code.fp  # the excerpt's fp is untouched
    data = tours.tour_json(tours.get("walk"))
    assert data["steps"][0]["notes"] == [{"from": 4, "to": 4, "text": "Shows the hit.", "cue": "loses health"}]
    assert data["steps"][1]["notes"] == []
    assert format_tour("app", data, 1).splitlines()[-1] == 'note 4: Shows the hit. (at "loses health")'
    assert '> note 4: Shows the hit. (at "loses health")' in tour_markdown("app", data)
    tours.edit_note("walk", 1, "3-4", "Both lines.")
    tours.edit_note("walk", 1, "4", "Changed.")  # same lines: replaced
    notes = tours.tour_json(tours.get("walk"))["steps"][0]["notes"]
    assert [(n["from"], n["to"], n["text"], n["cue"]) for n in notes] == [(3, 4, "Both lines.", None), (4, 4, "Changed.", None)]
    tours.edit_note("walk", 1, "3-4", None)
    assert len(tours.get("walk").steps[0].notes) == 1
    with pytest.raises(ValueError, match="outside"):
        tours.edit_note("walk", 1, "9", "No.")
    with pytest.raises(ValueError, match="no note"):
        tours.edit_note("walk", 1, "3", None)


def test_line_notes_follow_edits_and_old_tours_load(repo):
    root, tours = repo
    assert all(s.notes == [] for s in tours.get("walk").steps)  # a tour without notes loads
    tours.edit_note("walk", 1, "4", "The flash.")
    graph = setup(root, "import os\n\n\n" + CODE)
    note = tours_of(root, graph).tour_json(tours_of(root, graph).get("walk"))["steps"][0]["notes"][0]
    assert (note["from"], note["to"]) == (7, 7)


def test_put_takes_notes_in_todays_lines_and_counts_them_as_a_change(repo):
    _, tours = repo
    body = tours.tour_json(tours.get("walk"))
    sent = [{"key": s["key"], "title": s["title"], "text": s["text"], "targets": s["targets"],
             "code": s["code"] and {"at": s["code"]["at"], "lines": "+1..+2" if s["key"] == "k1" else "+0..+3",
                                    "mark": s["code"]["mark"]}, "notes": s["notes"]} for s in body["steps"]]
    sent[1]["notes"] = [{"from": 11, "to": 12, "text": "Makes one.", "cue": "makes"}]
    tour = tours.put("walk", {"title": "A walk", "steps": sent}, by="you")
    assert tour.steps[0].notes == [] and (tour.steps[0].by, tour.steps[0].ai) == ("you", False)
    assert [(n.first, n.last, n.cue) for n in tour.steps[1].notes] == [(1, 2, "makes")]
    assert (tour.steps[1].by, tour.steps[1].ai) == ("you", False)  # was an AI step: now yours
    with pytest.raises(ValueError):
        tours.put("walk", {"title": "A walk", "steps": [{**sent[1], "notes": [{"from": 11}]}]})


def test_cli_tour_notes(repo, monkeypatch, capsys, tmp_path):
    root, _ = repo
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "reg.json"))
    (tmp_path / "reg.json").write_text(json.dumps({"app": {"path": str(root)}}))
    main(["rescan", "app"])
    capsys.readouterr()
    main(["tour", "app", "walk", "--step", "1", "--note", "3-4", "Two lines.", "--cue", "health"])
    assert "walk step 1: note saved on lines 3-4 by claude (AI draft) (1 notes)" in capsys.readouterr().out
    main(["tour", "app", "walk", "--step", "1"])
    assert capsys.readouterr().out.splitlines()[-1] == 'note 3-4: Two lines. (at "health")'
    main(["tour", "app", "walk", "--step", "1", "--drop-note", "3-4"])
    assert "note removed on lines 3-4" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["tour", "app", "walk", "--note", "3", "x"])


def test_open_near_opens_the_classes_a_focused_function_calls(tmp_path):
    graph = setup(tmp_path, """class Player:
    def hit(self):
        pass


class World:
    def __init__(self):
        self.player = Player()

    def run(self):
        self.player.hit()
""")
    plain = compute_view(graph, level=5, focus=["m.World#run"])
    assert plain["focus"]["near"] == ["m.Player"] and list(plain["expanded"]) == ["m.World"]
    step = compute_view(graph, level=5, focus=["m.World#run"], open_near=True)
    assert list(step["expanded"]) == ["m.Player", "m.World"]


def test_start_flag_round_trips_and_old_tours_load_unchanged(repo):
    root, tours = repo
    assert tours.get("walk").start is False and "start" not in (root / ".coderstanding/tours/walk.md").read_text()
    assert tours.row_json(tours.get("walk"))["start"] is False
    tour = tours.get("walk")
    tour.start = True
    tours.save(tour)
    assert "\nstart: true\n" in (root / ".coderstanding/tours/walk.md").read_text()
    assert tours.tour_json(tours.get("walk"))["start"] is True
    body = {"title": "A walk", "steps": [{"key": "k1", "targets": ["Player#hit"], "text": "x", "title": "Hits"}]}
    assert tours.put("walk", body).start is True  # an editor save keeps the flag


STARTER = {
    "tool.py": "import a\n\n\ndef main():\n    a.run()\n\n\nif __name__ == '__main__':\n    main()\n",
    "a.py": "import b\n\n\ndef run():\n    b.go()\n",
    "b.py": "import c\n\n\ndef go():\n    c.work()\n    c.work()\n",
    "c.py": "def work():\n    pass\n",
}


def test_draft_starts_writes_a_start_tour_per_path_and_leaves_existing_ones(tmp_path):
    write_files(tmp_path, STARTER)
    graph = scan_repo(tmp_path, use_cache=False)
    tours = tours_of(tmp_path, graph)
    for module in STARTER:  # the guessed levels put the modules at different depths: all on level 1
        tours.notes.set(node_key(module[:-3]), "x", {"level": 1})
    drafted, kept = tours.draft_starts("Begin here")
    assert [(t.id, t.title, t.start, t.ai) for t in drafted] == [("start-tool", "Begin here", True, True)]
    assert [s.targets for s in drafted[0].steps] == [["tool"], ["a"], ["b"], ["c"]]
    assert "\nstart: true\n" in (tmp_path / ".coderstanding/tours/start-tool.md").read_text()
    assert tours.draft_starts() == ([], ["start-tool"])


def test_cli_draft_start_and_tours_mark_it(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    write_files(root, STARTER)
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "reg.json"))
    (tmp_path / "reg.json").write_text(json.dumps({"app": {"path": str(root)}}))
    main(["rescan", "app"])
    for module in STARTER:
        main(["set", "app", module[:-3], "level=1"])
    capsys.readouterr()
    main(["tour", "app", "--draft-start"])
    assert "drafted start-tool: 4 steps by claude (AI draft, start tour)" in capsys.readouterr().out
    main(["tours", "app"])
    assert "start-tool Start here: tool 4 steps [claude ai] start tour" in capsys.readouterr().out
    main(["tour", "app", "--draft-start"])
    assert "start-tool exists: left alone" in capsys.readouterr().out


def test_view_options_round_trip_and_old_tours_load_unchanged(repo):
    root, tours = repo
    path = root / ".coderstanding/tours/walk.md"
    old_text = path.read_text()
    assert tours.get("walk").view == {} and "view" not in old_text  # old tour: no view, file unchanged
    assert tours.tour_json(tours.get("walk"))["view"] == {} and tours.tour_json(tours.get("walk"))["steps"][0]["view"] == {}
    tours.set_view("walk", {"lines": "all", "hide_kind": ["signal", "dependency"], "selfcalls": False})
    tours.set_view("walk", {"boxtext": "full"}, number=2)
    text = path.read_text()
    assert "\nview: {lines: all, hide_kind: [signal, dependency], selfcalls: false}\n" in text
    assert "    view: {boxtext: full}\n" in text
    tour = tours.get("walk")
    assert tour.view == {"lines": "all", "hide_kind": ["signal", "dependency"], "selfcalls": False}
    assert [s.view for s in tour.steps] == [{}, {"boxtext": "full"}]
    assert tour.steps[1].ai and tour.steps[1].by == "claude"  # not content: the author stays
    data = tours.tour_json(tour)
    assert data["view"]["lines"] == "all" and data["steps"][1]["view"] == {"boxtext": "full"}
    tours.set_view("walk", {"lines": None})  # key= removes it
    assert "lines" not in tours.get("walk").view


def test_put_carries_view_options_and_keeps_them_when_an_older_editor_sends_none(repo):
    _, tours = repo
    step = {"key": "k1", "targets": ["Player#hit"], "text": "[[Player#hit]] loses health.", "title": "Hits",
            "code": {"at": "Player#hit", "lines": "+1..+2", "mark": [4]}}
    tour = tours.put("walk", {"title": "A walk", "view": {"external": 0}, "steps": [{**step, "view": {"lines": "all"}}]})
    assert tour.view == {"external": 0} and tour.steps[0].view == {"lines": "all"}
    assert tour.steps[0].by == "you"  # only the view changed: the step stays as it was
    tour = tours.put("walk", {"title": "A walk", "steps": [step]})  # an older editor: no view anywhere
    assert tour.view == {"external": 0} and tour.steps[0].view == {"lines": "all"}
    with pytest.raises(ValueError):
        tours.put("walk", {"title": "A walk", "view": {"lines": {"deep": 1}}, "steps": [step]})
    with pytest.raises(ValueError):
        tours.put("walk", {"title": "A walk", "view": "lines=all", "steps": [step]})


def test_bad_view_in_a_file_is_dropped_not_fatal(repo):
    root, tours = repo
    path = root / ".coderstanding/tours/walk.md"
    path.write_text(path.read_text().replace("title: A walk\n", "title: A walk\nview: {lines: all, Bad-Name: 1, deep: {a: 1}}\n"))
    assert tours.get("walk").view == {"lines": "all"}


def test_cli_tour_view(repo, monkeypatch, capsys, tmp_path):
    root, _ = repo
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "reg.json"))
    (tmp_path / "reg.json").write_text(json.dumps({"app": {"path": str(root)}}))
    main(["rescan", "app"])
    capsys.readouterr()
    main(["tour", "app", "walk", "--view", "lines=all", "selfcalls=0", "hide_kind=signal,dependency"])
    assert capsys.readouterr().out.strip() == "walk tour view: lines=all selfcalls=0 hide_kind=signal,dependency"
    main(["tour", "app", "walk", "--step", "2", "--view", "boxtext=full"])
    assert capsys.readouterr().out.strip() == "walk step 2 view: boxtext=full"
    main(["tour", "app", "walk"])
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "view (whole tour): lines=all selfcalls=0 hide_kind=signal,dependency"
    assert "view (this step): boxtext=full" in out
    main(["tour", "app", "walk", "--step", "2"])
    out = capsys.readouterr().out
    assert "view (whole tour): lines=all" in out and "view (this step): boxtext=full" in out
    main(["tour", "app", "walk", "--view", "lines="])
    assert capsys.readouterr().out.strip() == "walk tour view: selfcalls=0 hide_kind=signal,dependency"
    with pytest.raises(SystemExit):
        main(["tour", "app", "walk", "--view", "nonsense"])


def test_draft_text_keeps_hint_and_description_apart(repo):
    _, tours = repo
    tours.notes.set(node_key("m.Player"), "x", {"hint": "Holds health", "description": "Takes hits."})
    step = tours.draft("apart", "Apart", ["Player"]).steps[0]
    assert step.text == "[[Player]]: Holds health.\n\nTakes hits."
