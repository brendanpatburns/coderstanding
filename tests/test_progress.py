"""Progress tracks (progress.py, DESIGN 7g): built-in and own tracks, rings per box, bars per
domain, a spec's progress, the CLI, the API and the history per scan."""

import json

import pytest

from coderstanding import progress
from coderstanding.apps import App, find_app, load_graph
from coderstanding.cli import main
from coderstanding.notes import Notes, function_key, node_key
from coderstanding.planned import PLANNED
from coderstanding.scan import scan_repo
from coderstanding.server import Api
from coderstanding.views import compute_view
from helpers import write_files

FILES = {
    "game/a.py": "class Alpha:\n    def run(self):\n        return 1\n    def later(self):\n        pass\n"
                 "class Beta:\n    def go(self):\n        return Alpha().run()\n",
    "game/b.py": "def helper():\n    return 2\n",
    "tools/c.py": "def tool():\n    return 3\n",
    "tests/test_a.py": "from a import Alpha\n\ndef test_run():\n    assert Alpha().run() == 1\n",
    "data/config.json": "{}\n",
}
PROVEN = {"name": "Proven", "of": {"files": "game/**", "units": "functions"}, "done": {"tag": "proven"}}


@pytest.fixture
def graph(tmp_path):
    write_files(tmp_path, FILES)
    return scan_repo(tmp_path)


def tracked(notes, *tracks):
    notes.tracks = list(tracks)
    return notes


def numbers(summary, name):
    row = next(r for r in summary if r["name"] == name)
    return row["done"], row["total"]


def test_built_in_tracks(graph):
    notes = Notes()
    notes.set(node_key("a.Alpha"), "claude", {"hint": "the first one"})
    summary = progress.Progress(graph, notes).summary()
    assert [r["name"] for r in summary] == ["Described", "Tested", "Built"]
    # classes: modules a, b, c and the classes Alpha, Beta (no tests, no json)
    assert numbers(summary, "Described") == (1, 5)
    tested, total = numbers(summary, "Tested")
    assert total == 5 and tested >= 1  # Alpha is reached from the test
    assert numbers(summary, "Built") == (4, 5)  # `later` is a stub


def test_planned_items_count_as_not_built(graph):
    notes = Notes()
    notes.set(function_key("a.Beta", "stop"), "you", {"made": PLANNED}, ai=False)
    assert numbers(progress.Progress(graph, notes).summary(), "Built") == (4, 6)


def test_own_track_tag_and_files(graph):
    notes = tracked(Notes(), PROVEN)
    for target in ("a.Alpha#run", "c#tool"):
        notes.set(node_key(target), "proof", {"tags": ["proven"]}, ai=False)
    tracks = progress.Progress(graph, notes)
    assert numbers(tracks.summary(), "Proven") == (1, 4)  # tools/ is not in `of`
    assert tracks.todo(tracks.track("proven")) == ["a.Alpha#later", "a.Beta#go", "b#helper"]


def test_setting_replaces_built_in_and_bad_ones_are_listed(graph):
    notes = tracked(Notes(), {"name": "described", "done": {"field": "description", "by": "person"}},
                    {"name": "Broken", "done": {"tag": "x", "fact": "tested"}},
                    {"name": "Odd", "of": {"folder": "x"}, "done": {"tag": "x"}})
    notes.set(node_key("a.Alpha"), "claude", {"description": "AI draft"})
    notes.set(node_key("a.Beta"), "you", {"description": "mine"}, ai=False)
    tracks = progress.Progress(graph, notes)
    assert [t["name"] for t in tracks.tracks] == ["Tested", "Built", "described"]
    assert numbers(tracks.summary(), "described") == (1, 5)  # the AI draft does not count
    assert len(tracks.problems) == 2 and "exactly one" in tracks.problems[0] and "folder" in tracks.problems[1]


def test_of_filters_file_kind_lang_tests_and_domain(graph):
    notes = Notes()
    notes.set(node_key("a"), "you", {"domain": "Game"}, ai=False)
    notes.set(node_key("a.Alpha"), "you", {"domain": "Game"}, ai=False)
    count = lambda of: progress.Progress(graph, tracked(notes, {"name": "T", "of": of, "done": {"fact": "built"}})) \
        .numbers(progress.find_track(progress.app_tracks(notes.tracks)[0], "T"))["total"]
    assert count({"file_kind": "Code"}) == 5 and count({"file_kind": ["Data"]}) == 0
    assert count({"tests": True}) == 6
    assert count({"domain": "Game"}) == 2
    assert count({"lang": "gdscript"}) == 0


def test_by_domain_and_no_warnings(graph):
    notes = Notes()
    notes.set(node_key("a.Alpha"), "you", {"domain": "Game"}, ai=False)
    tracks = progress.Progress(graph, notes)
    rows = tracks.by_domain(tracks.track("Tested"))
    assert rows[0]["domain"] == "Game" and rows[0]["total"] == 1 and rows[-1]["domain"] is None
    clean = progress.Progress(graph, tracked(Notes(), {"name": "Clean", "done": {"fact": "no_warnings"}}))
    done, total = numbers(clean.summary(), "Clean")
    assert total == 5 and done < total  # tools.c is unused


def test_view_rings_per_box_and_domain_bars(graph):
    notes = tracked(Notes(), PROVEN)
    notes.set(node_key("a.Alpha#run"), "proof", {"tags": ["proven"]}, ai=False)
    for node_id in ("a", "a.Alpha", "a.Beta"):
        notes.set(node_key(node_id), "you", {"domain": "Game", "level": 1}, ai=False)
    notes.set(node_key("a.Alpha"), "you", {"level": 3}, ai=False)
    view = compute_view(graph, notes, level=1, track="Proven")
    rows = {n["id"]: n.get("progress") for n in view["nodes"]}
    assert view["progress"] == {"track": "Proven", "done": 1, "total": 4, "percent": 25.0}
    assert rows["a.Beta"] == {"done": 1, "total": 3, "percent": 33.3}  # + Alpha, folded into its user
    assert rows["a"] is None  # a module without functions: nothing to count, no ring
    assert rows.get("c") is None  # not counted by the track: no ring
    overview = compute_view(graph, notes, level=0, track="proven")["overview"]
    assert next(d for d in overview["domains"] if d["name"] == "Game")["progress"]["done"] == 1
    assert compute_view(graph, notes, level=1)["progress"] is None


def test_of_links_counts_class_links_as_their_functions(graph):
    notes = tracked(Notes(), PROVEN)
    notes.set(node_key("a.Alpha#run"), "proof", {"tags": ["proven"]}, ai=False)
    tracks = progress.Progress(graph, notes)
    assert tracks.of_links(tracks.track("Proven"), ["a.Alpha", "c"]) == progress.score(1, 2)
    assert tracks.of_links(tracks.track("Proven"), ["a.Alpha#run", "a→b"]) == progress.score(1, 1)
    assert tracks.of_links(tracks.track("Tested"), ["a.Alpha#run"])["total"] == 1


def test_check_track_messages():
    with pytest.raises(ValueError, match="name"):
        progress.check_track({"done": {"tag": "x"}})
    with pytest.raises(ValueError, match="unknown fact"):
        progress.check_track({"name": "x", "done": {"fact": "pretty"}})
    with pytest.raises(ValueError, match="units"):
        progress.check_track({"name": "x", "of": {"units": "lines"}, "done": {"tag": "x"}})
    with pytest.raises(ValueError, match="by"):
        progress.check_track({"name": "x", "done": {"tag": "x", "by": "person"}})


def test_history_delta_and_trend(tmp_path):
    progress.record_history(tmp_path, 1, "today", [{"name": "Proven", "done": 3, "total": 10}])
    progress.record_history(tmp_path, 2, "today", [{"name": "Proven", "done": 5, "total": 10}])
    progress.record_history(tmp_path, 2, "today", [{"name": "Proven", "done": 6, "total": 10}])  # same scan: replaced
    (tmp_path / progress.HISTORY_FILE).open("a").write("not json\n")
    history = progress.read_history(tmp_path)
    assert [r["scan"] for r in history] == [1, 2]
    assert progress.add_deltas([{"name": "Proven", "done": 7}], history, 2)[0]["delta"] == 4
    assert progress.add_deltas([{"name": "New", "done": 7}], history, 3)[0]["delta"] is None
    assert [t["percent"] for t in progress.trend(history, "Proven")] == [30.0, 60.0]


# --- CLI, API, rescan history ---

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    main(["add", "t", str(repo)])
    found = find_app("t", None)
    found.write_settings(tracks=[PROVEN])
    return found


def test_cli_progress(app, capsys):
    main(["set", "t", "a.Alpha#run", "tags=proven", "--by", "proof"])
    capsys.readouterr()
    main(["progress", "t"])
    out = capsys.readouterr().out
    assert "Proven    25.0% (1/4)" in out and "functions in game/** · done: tag proven" in out
    main(["progress", "t", "--track", "proven"])
    out = capsys.readouterr().out
    assert "not done (3):" in out and "  Beta#go" in out and "(no domain) 25.0% (1/4)" in out
    with pytest.raises(SystemExit, match="no track Nope"):
        main(["progress", "t", "--track", "Nope"])


def test_cli_progress_since_last_scan(app, capsys):
    main(["rescan", "t"])
    main(["set", "t", "a.Alpha#run", "tags=proven", "--by", "proof"])
    (app.root / "game" / "b.py").write_text("def helper():\n    return 22\n")
    main(["rescan", "t"])
    capsys.readouterr()
    main(["progress", "t"])
    assert "Proven    25.0% (1/4) · +1 since last scan" in capsys.readouterr().out
    main(["progress", "t", "--track", "Proven"])
    assert "trend: 0.0% → 25.0%" in capsys.readouterr().out


def test_cli_progress_spec(app, capsys):
    main(["set", "t", "a.Alpha#run", "tags=proven", "--by", "proof"])
    main(["spec", "t", "--add", "Alpha work", "--links", "Alpha"])
    capsys.readouterr()
    spec_id = next(p.stem for p in (app.data / "specs").glob("*.md"))
    main(["progress", "t", "--spec", spec_id])
    assert "Proven    50.0% (1/2)" in capsys.readouterr().out


def test_api_progress(app):
    notes = app.load_notes()
    notes.set(node_key("a.Alpha#run"), "proof", {"tags": ["proven"]}, ai=False)
    app.save_notes(notes)
    api = Api(app, graph=load_graph(app))
    answer = api.progress({"track": ["Proven"]})
    assert answer["track"] == "Proven" and answer["domains"][0]["total"] == 4
    assert numbers(answer["tracks"], "Proven") == (1, 4) and "delta" in answer["tracks"][0]
    assert api.progress({})["domains"] == []
    view = api.view({"level": ["1"], "track": ["Proven"]})
    assert view["progress"]["done"] == 1 and view["progress"]["delta"] is None and view["progress"]["trend"] == []
    assert "track" not in api.text({"level": ["1"], "track": ["Proven"]}).splitlines()[0]


def test_api_view_progress_has_delta_and_trend(app):
    main(["rescan", "t"])
    notes = app.load_notes()
    notes.set(node_key("a.Alpha#run"), "proof", {"tags": ["proven"]}, ai=False)
    app.save_notes(notes)
    (app.root / "game" / "b.py").write_text("def helper():\n    return 22\n")
    main(["rescan", "t"])
    view = Api(app, graph=load_graph(app)).view({"level": ["1"], "track": ["proven"]})["progress"]
    assert view["delta"] == 1 and [t["done"] for t in view["trend"]] == [0, 1]

