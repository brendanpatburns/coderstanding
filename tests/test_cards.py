"""Cards (cards.py, DESIGN 7, 7d): specs and memos: store, stages, priorities, authors, checks,
folding, search and filters, reverse links, CLI text."""

import pytest

from coderstanding.apps import App, rescan
from coderstanding.cards import FIELDS, SPEC, Cards, link_index, open_cards
from coderstanding.cli import main
from coderstanding.items import parse_front_matter
from coderstanding.views import compute_view
from helpers import write_files
from test_tours import CODE

# older spec files: `status` and `classes` (read as stage and links, old stage names mapped)
SPECS = {
    "game.md": "---\nid: game\ntitle: The game\nlevel: 1\nstatus: doing\nclasses: [World]\nby: sam\n---\nIt runs [[spec:hits]].\n",
    "hits.md": "---\nid: hits\ntitle: Hits, damage and flashes\nparent: game\nclasses: [Player#hit, Gone, World>Player]\n---\nPlayers lose health.\n",
    "heal.md": "---\nid: heal\ntitle: Healing\nparent: hits\nlevel: 4\nstatus: idea\nclasses: [Player]\nby: claude\n---\n",
    "tools.md": "---\nid: tools\ntitle: Tools\nstatus: done\nclasses: []\n---\n",
}
MEMOS = {
    "why.md": "---\nid: why\ntitle: Why players flash\npriority: low\nimportance: high\nlinks: [Player, World>Player]\n---\nSee [[Player#hit]].\n",
    "fix.md": "---\nid: fix\ntitle: Fix the heal bug\nstage: todo\npriority: high\nlinks: [Player#heal]\n---\n",
}


@pytest.fixture
def app(tmp_path):
    write_files(tmp_path / "repo", {"m.py": CODE,
                                    **{f".coderstanding/specs/{k}": v for k, v in SPECS.items()},
                                    **{f".coderstanding/memos/{k}": v for k, v in MEMOS.items()}})
    return App("app", tmp_path / "repo", tmp_path / "repo" / ".coderstanding")


@pytest.fixture
def specs(app):
    return open_cards(app, rescan(app), app.load_notes(), "spec")


@pytest.fixture
def memos(app, specs):
    return open_cards(app, specs.graph, specs.notes, "memo")


def text_of(app, card_id, folder="specs"):
    return (app.data / folder / f"{card_id}.md").read_text()


def test_read_old_files_levels_stages_and_drafts(specs):
    assert [s.id for s in specs.roots()] == ["game", "tools"]
    hits, heal = specs.get("hits"), specs.get("heal")
    assert hits.title == "Hits, damage and flashes"  # a plain title with commas (block scalar)
    assert (specs.level(specs.get("game")), specs.level(hits), specs.level(heal)) == (1, 2, 4)
    stages = {i: specs.stage(c) for i, c in specs.cards.items()}
    assert stages == {"game": "in-progress", "hits": "idea", "heal": "idea", "tools": "implemented"}
    assert hits.links == ["Player#hit", "Gone", "World>Player"]
    assert heal.ai == set(FIELDS)  # by: claude, no ai = all AI
    assert hits.by == "you" and not hits.ai
    assert specs.problems(hits) == ["unknown link: Gone"]


def test_memo_kind_stages_default_na(memos):
    assert [c.id for c in memos.roots()] == ["fix", "why"]
    assert memos.stage(memos.get("why")) == "na" and memos.stage(memos.get("fix")) == "todo"
    data = memos.list_json()
    assert data["stages"] == ["todo", "in-progress", "done", "na"] and data["stage_labels"]["na"] == "N/A"
    assert data["items"]["why"]["key"] == "memo:why"
    with pytest.raises(ValueError, match="stage must be one of todo"):
        memos.write("why", {"stage": "implemented"})


def test_fold_zoom_search_filters_and_sorts(specs, memos):
    boxes, _ = specs.tree(level=1)
    game = boxes[0]
    assert (game["id"], game["finer"], game["folded"], game["children"]) == ("game", 2, ["hits", "heal"], [])
    assert game["folded_links"] == ["Player#hit", "Gone", "World>Player", "Player"]
    assert game["focus"] == ["m.World", "m.Player#hit", "m.World→m.Player", "m.Player"]  # Gone left out
    boxes, crumbs = specs.tree(zoom="hits")
    assert [b["id"] for b in boxes] == ["hits"] and boxes[0]["children"][0]["id"] == "heal"
    assert crumbs[-1] == {"id": "hits", "title": "Hits, damage and flashes"}
    assert specs.list_json(query="player health")["hits"] == [
        {"kind": "spec", "id": "hits", "title": "Hits, damage and flashes",
         "path": "The game › Hits, damage and flashes", "stage": "idea", "stage_label": "idea",
         "priority": None, "importance": None}]
    assert [h["id"] for h in specs.list_json(stage="idea")["hits"]] == ["hits", "heal"]
    assert [h["id"] for h in specs.list_json(stage="done")["hits"]] == ["tools"]  # old name mapped
    specs.write("tools", {"priority": "high", "importance": "low"})
    specs.write("heal", {"priority": "medium", "importance": "high"})
    assert [c.id for c in specs.pick(sort="priority")] == ["tools", "heal", "game", "hits"]
    assert [c.id for c in specs.pick(sort="importance")] == ["heal", "tools", "game", "hits"]
    assert [c.id for c in specs.pick(importance="high")] == ["heal"]
    assert [c.id for c in memos.pick(stage="na")] == ["why"]
    with pytest.raises(ValueError, match="sort must be"):
        specs.pick(sort="size")


def test_links_both_ways(app, specs, memos):
    index = link_index(app, specs.graph, specs.notes)
    assert [h["id"] for h in index["m.Player"]] == ["hits", "heal", "fix", "why"]  # functions count for the class
    assert [(h["kind"], h["id"]) for h in index["m.World→m.Player"]] == [("spec", "hits"), ("memo", "why")]
    assert [h["id"] for h in index["m.World"]] == ["game"]
    assert "Gone" not in index
    view = compute_view(specs.graph, specs.notes, level=9, badges={k: len(v) for k, v in index.items()})
    assert {n["id"]: n["cards"] for n in view["nodes"] if n["cards"]} == {"m.Player": 4, "m.World": 1}
    assert {e["key"]: e["cards"] for e in view["edges"]} == {"m.World→m.Player": 2}
    assert "cards" not in compute_view(specs.graph, specs.notes, level=9)["nodes"][0]


def test_write_round_trip_ids_and_order(app, specs):
    card, kept = specs.write(specs.new_id("The game"), {"title": "The game", "parent": "game",
                                                        "links": ["World", "m.World>m.Player"],
                                                        "stage": "review", "priority": "low",
                                                        "text": "Again."})
    assert card.id == "the-game" and kept == []
    again = Cards(SPEC, app.data, specs.graph, specs.notes)
    assert vars(again.get("the-game")) == vars(card)
    specs.write("the-game", {}, position=0)
    assert [s.id for s in Cards(SPEC, app.data, specs.graph, specs.notes).children("game")] == ["the-game", "hits"]
    meta, body = parse_front_matter(text_of(app, "the-game"))
    assert meta == {"id": "the-game", "title": "The game", "parent": "game", "stage": "review",
                    "priority": "low", "order": 0, "links": ["World", "m.World>m.Player"],
                    "by": "you"} and body == "Again."
    specs.write("game", {"title": "The game, again"})  # rewritten in the new names
    meta = parse_front_matter(text_of(app, "game"))[0]
    assert (meta["stage"], meta["links"], "status" in meta, "classes" in meta) == ("in-progress", ["World"], False, False)


def test_authors_ai_drafts_and_accept(app, specs):
    card, kept = specs.write("tools", {"stage": "review", "text": "AI text"}, by="claude", ai=True)
    assert kept == ["stage"] and specs.stage(card) == "implemented"  # a human value is kept
    assert card.text == "AI text" and card.ai == {"text"}
    card, _ = specs.write("tools", {"text": "AI text", "links": ["World"]}, by="you")
    assert card.ai == {"text"}  # sent back unchanged: still the AI's
    card, kept = specs.write("hits", {"stage": "todo", "priority": "high"}, by="claude", ai=True)
    assert kept == [] and card.ai == {"stage", "priority"}  # unset fields: the AI may fill them
    heal, _ = specs.write("heal", {"title": "Healing up"}, by="you")
    assert "title" not in heal.ai and "text" in heal.ai
    assert specs.accept("heal").ai == set()
    assert "ai:" not in text_of(app, "heal")
    new, _ = specs.write("draft", {"title": "Draft", "links": ["Player"]}, by="claude", ai=True)
    assert "ai: [title, links]" in text_of(app, "draft")


def test_checks_refuse_cycles_levels_and_bad_values(specs):
    with pytest.raises(ValueError, match="cycle"):
        specs.write("game", {"parent": "heal"})
    with pytest.raises(ValueError, match="shallower"):
        specs.write("hits", {"level": 1})
    with pytest.raises(ValueError, match="no spec nope"):
        specs.write("hits", {"parent": "nope"})
    with pytest.raises(ValueError, match="stage must be"):
        specs.write("hits", {"stage": "maybe"})
    with pytest.raises(ValueError, match="priority must be"):
        specs.write("hits", {"priority": "P1"})
    with pytest.raises(ValueError, match="needs a title"):
        specs.write("fresh", {"stage": "idea"})
    specs.write("heal", {"parent": "tools", "level": 2})
    specs.write("heal", {"parent": "hits"})  # moved under a level-2 parent: follows it
    assert specs.get("heal").level is None and specs.level(specs.get("heal")) == 3


def test_delete_moves_children_up_and_broken_files(app, specs):
    specs.write("extra", {"title": "Extra", "parent": "game"})
    assert specs.delete("hits") == ["heal"]
    assert [s.id for s in specs.children("game")] == ["heal", "extra"]
    (app.data / "specs" / "bad.md").write_text("no front matter\n")
    again = Cards(SPEC, app.data, specs.graph, specs.notes)
    assert "bad" in again.broken and again.all_problems()[0][0] == "bad"
    with pytest.raises(ValueError, match="cannot be read"):
        again.write("bad", {"title": "x"})


def run_cli(capsys, *args):
    main(list(args))
    return capsys.readouterr().out


def test_cli_specs_tree_filters_show_and_writes(app, capsys):
    rescan(app)
    repo = str(app.root)
    out = run_cli(capsys, "specs", repo)
    assert out.splitlines()[:4] == [
        "repo specs: 4, levels 1-4",
        "game: The game [L1, in-progress, 1 links]",
        "  hits: Hits, damage and flashes [L2, idea, 3 links, 1 problems]",
        "    heal: Healing [L4, idea, 1 links, ai]"]
    assert "problem: hits: unknown link: Gone" in out
    assert "game: The game [L1, in-progress, 1 links] +2 finer" in run_cli(capsys, "specs", repo, "--level", "1")
    run_cli(capsys, "spec", repo, "heal", "--set", "priority=high", "--by", "you")
    assert run_cli(capsys, "specs", repo, "--stage", "idea", "--sort", "priority").splitlines()[:2] == [
        "heal: The game › Hits, damage and flashes › Healing [idea, p:high]",
        "hits: The game › Hits, damage and flashes [idea]"]
    out = run_cli(capsys, "spec", repo, "hits")
    assert out.splitlines()[:4] == ["repo spec hits: Hits, damage and flashes [by you, L2, idea, 3 links]",
                                    "path: The game › Hits, damage and flashes", "children: heal",
                                    "links: Player#hit, Gone, World>Player"]
    out = run_cli(capsys, "spec", repo, "--add", "Save games", "--parent", "game", "--links",
                  "World,World>Player", "--stage", "todo", "--importance", "high", "--text", "Save to disk.")
    assert out.startswith("saved save-games by claude (AI draft)")
    meta, body = parse_front_matter(text_of(app, "save-games"))
    assert (meta["links"], meta["stage"], meta["importance"], body) == (
        ["World", "World>Player"], "todo", "high", "Save to disk.")
    out = run_cli(capsys, "spec", repo, "tools", "--set", "status=doing", "classes+=World")  # old names
    assert "kept your stage" in out
    assert parse_front_matter(text_of(app, "tools"))[0]["links"] == ["World"]
    run_cli(capsys, "spec", repo, "save-games", "--accept")
    assert "ai:" not in text_of(app, "save-games")
    assert run_cli(capsys, "spec", repo, "game", "--delete") == "deleted game; moved up: hits, save-games\n"
    assert "spec problem: hits: unknown link: Gone" in run_cli(capsys, "warnings", repo)
    with pytest.raises(SystemExit, match="level must be"):
        main(["spec", repo, "hits", "--set", "level=0"])


def test_cli_memos_and_linked_lines(app, capsys):
    rescan(app)
    repo = str(app.root)
    assert run_cli(capsys, "memos", repo).splitlines() == [
        "repo memos: 2, levels 1-1", "fix: Fix the heal bug [L1, todo, p:high, 1 links]",
        "why: Why players flash [L1, na, p:low, i:high, 2 links]"]
    run_cli(capsys, "memo", repo, "--add", "Ask about saves", "--links", "World")
    assert "title: Ask about saves" in text_of(app, "ask-about-saves", "memos")
    out = run_cli(capsys, "node", repo, "Player")
    assert out.splitlines()[-2:] == ["specs: hits [idea], heal [idea]",
                                     "memos: fix [todo, p:high], why [na, p:low, i:high]"]
    out = run_cli(capsys, "zoom", repo, "World", "Player")
    assert out.splitlines()[-2:] == ["specs: hits [idea]", "memos: why [na, p:low, i:high]"]
