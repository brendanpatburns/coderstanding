"""Field rules: built-in guidance, per-project overrides, `fields`, `guide`, `set` reminders, API."""

import json
import shutil
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from coderstanding import field_rules, guide
from coderstanding.apps import App
from coderstanding.cli import main
from coderstanding.server import HOST, Api, make_server
from test_cli_views import FIXTURE, run  # noqa: F401

PLAIN = "Sends chat messages between players; everything that talks to the server goes through it"
CODEY = "Uses a Dictionary<int, Peer> and calls rpc_id() per message"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "game"
    shutil.copytree(FIXTURE, repo)
    main(["add", "game", str(repo)])
    return repo


def data_of(repo):
    return repo / ".coderstanding"


# --- resolver ---

def test_defaults_cover_every_note_field_with_all_parts(tmp_path):
    rules = field_rules.effective(tmp_path)
    assert list(rules["fields"]) == ["hint", "description", "review", "rules", "status", "tags", "level", "domain"]
    for name, f in rules["fields"].items():
        assert all(f[k] for k in field_rules.TEXT_KEYS), name
        assert f["custom"] == []
    assert "new developer" in rules["rule"] and rules["house"] == ""
    assert "claude" not in json.dumps(rules).lower()


def test_store_fields_all_have_rules():
    from coderstanding.notes import TEXT_FIELDS
    assert set(TEXT_FIELDS) | {"level", "domain"} <= set(field_rules.defaults()["fields"])


def test_partial_override_reset_and_house_rules(tmp_path):
    rules = field_rules.change(tmp_path, {"hint.max": "120", "hint.what": "A tiny line", "house": "Use our product words."},
                               by="ana")
    assert rules["fields"]["hint"]["max"] == 120 and rules["fields"]["hint"]["what"] == "A tiny line"
    assert rules["fields"]["hint"]["custom"] == ["what", "max"]
    assert rules["fields"]["hint"]["who"] == field_rules.defaults()["fields"]["hint"]["who"]
    assert rules["house"] == "Use our product words."
    assert rules["fields"]["description"]["custom"] == []
    text = (tmp_path / "fields.md").read_text()
    assert "## hint" in text and "by: ana" in text and "## description" not in text
    field_rules.change(tmp_path, {"hint.what": ""})  # empty = built-in again
    assert field_rules.effective(tmp_path)["fields"]["hint"]["custom"] == ["max"]
    field_rules.change(tmp_path, resets=["hint"])
    assert field_rules.effective(tmp_path)["fields"]["hint"]["custom"] == []
    field_rules.change(tmp_path, resets=["all"])
    assert not (tmp_path / "fields.md").exists()


def test_bad_names_and_values_are_refused(tmp_path):
    for sets in ({"hint.max": "lots"}, {"nope.what": "x"}, {"hint.colour": "x"}, {"hint": "x"}, {"hint.code_check": "maybe"}):
        with pytest.raises(ValueError):
            field_rules.change(tmp_path, sets)
    with pytest.raises(ValueError):
        field_rules.change(tmp_path, resets=["nope"])


def test_hand_edited_file_is_read(tmp_path):
    (tmp_path / "fields.md").write_text("# Field rules\n\n## house rules\nBe kind.\n\n## HINT\nmax: 60\nwhat: Short.\nbogus: 1\n\n## unknown\nmax: 3\n")
    rules = field_rules.effective(tmp_path)
    assert rules["fields"]["hint"]["max"] == 60 and rules["house"] == "Be kind."


# --- the heuristics ---

def test_code_like_versus_plain():
    assert not field_rules.looks_code_heavy(PLAIN)
    assert not field_rules.looks_code_heavy("Owns the shop's ChatSystem and shows prices to the player")
    for text in (CODEY, "calls get_peer() and then send_all()", "```x = 1```", "Vector2 pos via Player::update"):
        assert field_rules.looks_code_heavy(text), text


def test_warnings_follow_the_rules(tmp_path):
    rules = field_rules.effective(tmp_path)
    assert field_rules.field_warnings(rules, {"hint": PLAIN}) == []
    assert any("code" in w for w in field_rules.field_warnings(rules, {"hint": CODEY}))
    assert any("characters" in w for w in field_rules.field_warnings(rules, {"hint": "word " * 30}))
    assert field_rules.field_warnings(rules, {"review": CODEY, "tags": CODEY}) == []
    loose = field_rules.change(tmp_path, {"hint.max": "0", "hint.code_check": "off"})
    assert field_rules.field_warnings(loose, {"hint": CODEY + " " * 0 + "x" * 200}) == []


# --- docs ---

def test_ai_guide_section_matches_built_in_rules():
    text = (Path(__file__).parent.parent / "docs" / "AI_GUIDE.md").read_text()
    start, end = text.index(guide.FIELDS_BEGIN), text.index(guide.FIELDS_END)
    expected = field_rules.guide_markdown({**field_rules.defaults(), "house": "",
                                           "fields": {n: dict(f) for n, f in field_rules.defaults()["fields"].items()}})
    assert text[start + len(guide.FIELDS_BEGIN):end].strip() == expected.strip(), "regenerate the AI_GUIDE field section"


def test_guide_shows_project_rules_and_snippet_mentions_them(app, capsys):
    plain = "\n".join(run(capsys, "guide", "--app", "game"))
    assert "new developer" in plain and "house rules" not in plain.lower().split("writing note fields")[1][:600]
    main(["fields", "game", "--set", "house=Say 'pod' not 'node'.", "hint.max=70", "--by", "ana"])
    capsys.readouterr()
    text = "\n".join(run(capsys, "guide", "--app", "game"))
    assert "Say 'pod' not 'node'." in text and "Max 70 characters" in text
    assert "own rules for writing note fields" in "\n".join(run(capsys, "guide", "--snippet", "--app", "game"))
    assert "own rules" not in "\n".join(run(capsys, "guide", "--snippet", "--app", "other"))


# --- CLI ---

def test_fields_command_round_trip(app, capsys):
    shown = "\n".join(run(capsys, "fields", "game"))
    assert "project overrides: none" in shown and "For:" in shown
    out = "\n".join(run(capsys, "fields", "game", "--set", "hint.max=120", "description.code_check=off", "--by", "ana"))
    assert "project overrides: hint (max); description (code_check)" in out
    assert "by: ana" in (data_of(app) / "fields.md").read_text()
    out = "\n".join(run(capsys, "fields", "game", "--reset", "hint"))
    assert "project overrides: description (code_check)" in out
    assert "project overrides: none" in "\n".join(run(capsys, "fields", "game", "--reset", "all"))
    with pytest.raises(SystemExit):
        main(["fields", "game", "--set", "hint.max=lots"])


def test_set_help_and_curate_carry_the_reminder(app, capsys):
    with pytest.raises(SystemExit):
        main(["set", "--help"])
    help_text = capsys.readouterr().out
    assert "not very technical" in help_text and "not how it works" in help_text
    lines = run(capsys, "curate", "game")
    assert lines[1].startswith("reminder: hint/description are for a new developer")
    main(["fields", "game", "--set", "house=Use plain words."])
    capsys.readouterr()
    assert "house rules: Use plain words." in run(capsys, "curate", "game")


def test_set_reminds_ai_but_not_humans_and_never_refuses(app, capsys):
    lines = run(capsys, "set", "game", "Player", f"hint={PLAIN}", "description=Plain words for newcomers.")
    assert lines == ["set 1 by claude (AI draft)"]
    lines = run(capsys, "set", "game", "Player", f"hint={CODEY}")
    assert lines[0] == "set 1 by claude (AI draft)"  # still written
    assert lines[1].startswith("reminder node:Player: hint looks like code talk")
    assert any(l.startswith("reminder: hint/description") for l in lines)
    lines = run(capsys, "set", "game", "Player", "hint=" + "long " * 30, "--by", "ana")
    assert lines == ["set 1 by ana"]
    lines = run(capsys, "set", "game", "Enemy", "hint=" + "long " * 30)
    assert any("characters (max 100)" in l for l in lines)


# --- API ---

@pytest.fixture
def served(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    app = App("app", repo, repo / ".coderstanding")
    from coderstanding.apps import rescan
    srv = make_server(Api(app, graph=rescan(app)), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://{HOST}:{srv.server_port}", app
    srv.shutdown()
    srv.server_close()


def call(base, path, data=None, method="GET", content_type="application/json", headers=None):
    body = None if data is None else json.dumps(data).encode()
    request = Request(base + path, data=body, method=method,
                      headers={**({"Content-Type": content_type} if content_type else {}), **(headers or {})})
    try:
        with urlopen(request) as response:
            return json.loads(response.read())
    except HTTPError as error:
        return {"status": error.code, **json.loads(error.read())}


def test_api_get_put_and_guards(served, monkeypatch):
    base, app = served
    monkeypatch.setenv("CODERSTANDING_USER", "bob")
    assert call(base, "/api/fields")["fields"]["hint"]["custom"] == []
    got = call(base, "/api/fields", {"set": {"hint.max": "90"}, "house": "Plain."}, "PUT")
    assert got["fields"]["hint"]["max"] == 90 and got["house"] == "Plain."
    assert "by: bob" in (app.data / "fields.md").read_text()
    assert call(base, "/api/fields")["fields"]["hint"]["custom"] == ["max"]
    assert call(base, "/api/fields", {"reset": ["hint"]}, "PUT")["fields"]["hint"]["custom"] == []
    assert call(base, "/api/fields", {"set": {"hint.max": "x"}}, "PUT")["status"] == 400
    assert call(base, "/api/fields", {"bogus": 1}, "PUT")["status"] == 400
    assert call(base, "/api/fields", {"set": {}}, "PUT", content_type="text/plain")["status"] == 415
    assert call(base, "/api/fields", {"set": {}}, "PUT", headers={"Origin": "http://evil.example"})["status"] == 403
