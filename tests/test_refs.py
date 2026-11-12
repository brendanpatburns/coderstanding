"""Location refs (refs.py, DESIGN 7e): parse / write, find in pasted text, resolve, API, CLI."""

import json
from pathlib import Path
from urllib.parse import quote

import pytest

from coderstanding.apps import App, REGISTRY_ENV, rescan
from coderstanding.cli import main
from coderstanding.refs import find_refs, format_ref, parse_ref, resolve_json
from coderstanding.tours import open_tours
from coderstanding.server import Api
from helpers import write_files
from test_cards import MEMOS, SPECS
from test_tours import CODE, TOUR
from test_tours_api import call, serve

EXAMPLES = json.loads((Path(__file__).parent / "fixtures" / "refs.json").read_text())
README = "# Intro\nhello\n\n## Setup\nrun it\n"


@pytest.mark.parametrize("example", EXAMPLES, ids=[e["ref"] for e in EXAMPLES])
def test_fixture_examples_parse_find_and_write_back(example):
    assert parse_ref(example["ref"]) == example["parsed"]
    assert find_refs(example["ref"]) == [example["parsed"]]
    assert format_ref(example["parsed"]) == example.get("canonical", example["ref"])


def test_find_refs_ignores_junk_and_reads_many_lines():
    text = ("Please look at this:\n"
            "- `@demo src/a.py:1-2 (Peer#f +0..+1)` and then more words\n"
            "mail me at ana@example.com now\n"
            "@john hello there\n"
            "see @demo tour x step 2, thanks\n"
            "> @demo Peer#f\n")
    found = find_refs(text)
    assert [p["kind"] for p in found] == ["lines", "target"]  # prose after a ref word is no ref
    assert found[0]["hint_offset"] == [0, 1]


def test_bare_target_takes_the_default_app():
    assert parse_ref("Peer#f", "demo") == {"app": "demo", "kind": "target", "target": "Peer#f"}
    assert parse_ref("not a ref") is None


@pytest.fixture
def desk(tmp_path):
    write_files(tmp_path / "repo", {
        "m.py": CODE, "README.md": README, ".coderstanding/tours/walk.md": TOUR,
        **{f".coderstanding/specs/{k}": v for k, v in SPECS.items()},
        **{f".coderstanding/memos/{k}": v for k, v in MEMOS.items()}})
    app = App("app", tmp_path / "repo", tmp_path / "repo" / ".coderstanding")
    graph = rescan(app)
    tours = open_tours(app, graph, app.load_notes())
    tour = tours.get("walk")
    for step in tour.steps:
        tours.stamp(step)
    tours.save(tour)
    tours.comments.add("tour:walk#k1", "Explain hp", "ana", line=3)
    srv, base = serve(Api(app, graph=graph))
    yield base, app
    srv.shutdown()
    srv.server_close()


def ref(base, text, app="app"):
    return call(base, f"/api/ref?app={app}&ref={quote(text)}")


def test_lines_inside_a_function_name_their_unit(desk):
    base, _ = desk
    got = ref(base, "@app m.py:3-4 (m.Player#hit +1..+2)")
    assert got == {"ok": True, "kind": "lines", "app": "app", "path": "m.py", "lines": [3, 4],
                   "target": "m.Player#hit", "view": {"select": "m.Player#hit", "file": "m.py", "hl": "3-4"}}


def test_moved_function_is_found_again_by_its_offsets(desk):
    base, app = desk
    path = app.root / "m.py"
    path.write_text("# a\n# b\n# c\n" + CODE)
    api_graph = rescan(app)
    parsed = parse_ref("@app m.py:3-4 (m.Player#hit +1..+2)")
    out = resolve_json(app, api_graph, app.load_notes(), parsed)
    assert out["ok"] and out["lines"] == [6, 7] and "moved" in out["warning"]
    plain = resolve_json(app, api_graph, app.load_notes(), parse_ref("@app m.py:3-4 (m.Player#hit)"))
    assert plain["lines"] == [3, 4] and "no longer in m.Player#hit" in plain["warning"]


def test_node_function_and_heading(desk):
    base, _ = desk
    node = ref(base, "@app m.Player")
    assert (node["kind"], node["target"], node["lines"], node["view"]) == (
        "node", "m.Player", [1, 7], {"select": "m.Player"})
    fn = ref(base, "@app Player#hit")
    assert (fn["kind"], fn["target"], fn["lines"]) == ("function", "m.Player#hit", [2, 4])
    head = ref(base, "@app README.md#Setup")
    assert (head["kind"], head["path"], head["lines"]) == ("heading", "README.md", [4, 5])


def test_tour_step_lines_comment_spec_memo(desk):
    base, app = desk
    assert ref(base, "@app tour walk")["view"] == {"tab": "tours", "tour": "walk"}
    step = ref(base, "@app tour walk step 1")
    assert (step["kind"], step["step"], step["lines"], step["view"]) == (
        "step", 1, [3, 4], {"tab": "tours", "tour": "walk", "step": 1})
    marked = ref(base, "@app tour walk step 1 lines 4")
    assert marked["lines"] == [4, 4] and marked["view"]["hl"] == "4-4"
    comment = call(base, "/api/comments?app=app&target=tour:walk")["comments"][0]["id"]
    got = ref(base, f"@app comment {comment}")
    assert (got["kind"], got["tour"], got["step"], got["view"]["comment"]) == ("comment", "walk", 1, comment)
    assert ref(base, "@app spec hits")["view"] == {"specs": "1", "spec": "hits"}
    assert ref(base, "@app memo why")["view"] == {"notes": "1", "note": "why"}


def test_misses_are_404_with_did_you_mean_and_app_case_is_ignored(desk):
    base, _ = desk
    miss = ref(base, "@app tour wlak")
    assert miss["status"] == 404 and miss["ok"] is False and "walk" in miss["did_you_mean"]
    assert ref(base, "@app Plyer#hit")["status"] == 404
    assert ref(base, "@app tour walk step 9")["status"] == 404
    assert ref(base, "@app spec nope")["status"] == 404
    assert ref(base, "@APP spec hits", app="APP")["ok"] is True
    assert ref(base, "nonsense words")["status"] == 404


def test_cli_ref_prints_like_the_other_commands(desk, monkeypatch, capsys, tmp_path):
    _, app = desk
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "reg.json"))
    (tmp_path / "reg.json").write_text(json.dumps({"app": {"path": str(app.root)}}))
    capsys.readouterr()
    main(["ref", "see `@app m.py:3-4` and\n@APP tour walk step 1 lines 4\n@app Plyer#hit"])
    out = capsys.readouterr().out
    assert "app read m.py:3-4" in out and "3          self.hp -= 1" in out
    assert "1/2 Hits {Player#hit}" in out and ">" in out.split("1/2 Hits")[1]
    assert "error: " in out and "did you mean" in out
    main(["read", "app", "@app Player#hit", "m.py:12-13"])
    out = capsys.readouterr().out
    assert "app read m.Player#hit" in out and "app read m.py:12-13" in out
    with pytest.raises(SystemExit):
        main(["ref", "no refs here"])
