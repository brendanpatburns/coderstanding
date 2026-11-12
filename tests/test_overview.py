"""The 30-second overview of level 0 (overview.py): sentence note, cards, order, slim text."""
from coderstanding import textout
from coderstanding.apps import AppError, find_id
from coderstanding.notes import Notes, overview_id
from coderstanding.views import compute_view
from test_views import make_graph


def domain_notes():
    graph = make_graph({"Net": 1, "Peer": 2, "Ui": 1, "Deep": 3, "Main": 1},
                       [("Net", "Peer", "composition"), ("Peer", "Ui", "signal", ["joined"]),
                        ("Ui", "Net", "dependency"), ("Main", "Ui", "dependency"),
                        ("Deep", "Net", "dependency")])
    graph.nodes["Main"].flags.append("entry")
    notes = Notes()
    for cls, domain in (("Net", "Networking"), ("Peer", "Networking"), ("Deep", "Networking"),
                        ("Ui", "UI"), ("Main", "Start")):
        notes.set(f"node:{cls}", "claude", {"domain": domain})
    return graph, notes


def test_cards_order_keys_and_talks():
    graph, notes = domain_notes()
    notes.set("node:domain:UI", "claude", {"description": "What people see"})
    notes.set("edge:domain:UI→domain:Networking", "claude", {"hint": "asks for data"})
    ov = compute_view(graph, notes, level=0)["overview"]
    assert [d["name"] for d in ov["domains"]] == ["Start", "UI", "Networking"]  # entry first, then its lines
    net = ov["domains"][2]
    assert net["classes"] == 3 and net["keys"][0]["name"] == "Net"  # most used first
    assert net["keys"][0]["used_by"] == 2
    ui = ov["domains"][1]
    assert ui["description"] == "What people see"
    assert ui["talks"] == [{"to": "Networking", "hint": "asks for data"}]
    assert ov["sentence"] is None and ov["generated"].startswith("Start uses UI.")


def test_sentence_note_on_domain_star():
    graph, notes = domain_notes()
    notes.set("node:" + overview_id(), "claude", {"description": "Start leads to UI."}, True, graph)
    ov = compute_view(graph, notes, level=0)["overview"]
    assert ov["sentence"] == "Start leads to UI." and ov["ai"] is True
    assert not notes.lost(graph)  # the pseudo domain is a known key
    assert find_id(graph, notes, "domain:*") == "domain:*"


def test_no_overview_without_domains_or_off_level_zero():
    graph, notes = domain_notes()
    assert compute_view(graph, Notes(), level=0)["overview"] is None
    assert compute_view(graph, notes, level=1)["overview"] is None


def test_removed_domain_keeps_harmless_notes():
    graph, notes = domain_notes()
    notes.set("node:domain:Start", "claude", {"description": "old"})
    notes.set("node:Main", "claude", {"domain": None})  # the domain no longer exists
    ov = compute_view(graph, notes, level=0)["overview"]
    assert "Start" not in [d["name"] for d in ov["domains"]]


def test_text_prints_sentence_then_a_line_per_domain():
    graph, notes = domain_notes()
    text = textout.view_text("app", graph, notes, level=0)
    lines = text.splitlines()
    head = next(i for i, l in enumerate(lines) if l.startswith("overview"))
    assert lines[head].startswith("overview (generated, none written): Start uses UI.")
    assert lines[head + 1].startswith("  Start (1)") and "| talks to: UI" in lines[head + 1]
    assert lines[head + 3].startswith("  Networking (3)") and "key: Net" in lines[head + 3]
    assert head < next(i for i, l in enumerate(lines) if "~>" in l or "→" in l)
    assert not any(l.startswith("overview") for l in textout.view_text("app", graph, notes, level=1).splitlines())


def test_text_marks_ai_draft_sentence():
    graph, notes = domain_notes()
    notes.set("node:domain:*", "claude", {"description": "Start leads to UI."}, True, graph)
    assert "overview (AI draft): Start leads to UI." in textout.view_text("app", graph, notes, level=0)
