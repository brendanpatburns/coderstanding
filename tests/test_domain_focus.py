"""Domain focus (DESIGN 6b): one domain's classes at a level, the other domains as faded cards."""
from coderstanding.notes import Notes
from coderstanding.server import view_args
from coderstanding.views import compute_view

from test_views import edge_of, make_graph, node_ids, pairs


def focus_graph():
    graph = make_graph(
        {"Net": 1, "Peer": 2, "Ui": 1, "Hud": 2, "Loose": 1, "Deep": 3, "lib": (1, "external", [])},
        [("Net", "Peer", "composition"), ("Peer", "Ui", "signal", ["joined"]),
         ("Ui", "Hud", "composition"), ("Hud", "Net", "dependency"),
         ("Loose", "Net", "dependency"), ("Net", "Deep", "composition"), ("Deep", "Ui", "dependency"),
         ("Net", "lib", "dependency")])
    notes = Notes()
    for cls, domain in (("Net", "Networking"), ("Peer", "Networking"), ("Ui", "UI"), ("Hud", "UI")):
        notes.set(f"node:{cls}", "claude", {"domain": domain})
    return graph, notes


def test_focus_shows_one_domain_and_faded_cards():
    graph, notes = focus_graph()
    view = compute_view(graph, notes, level=1, domain="Networking")
    assert node_ids(view, faded=False) == {"Net"}
    assert node_ids(view, faded=True) == {"domain:UI", "domain:Unsorted", "lib"}
    assert view["domain"] == "Networking" and view["counts"]["shown"] == 1
    # Deep (no domain, below the level) folds into its owner Net; Ui's line comes through it
    assert pairs(view) == {"Net→domain:UI", "domain:UI→Net", "domain:Unsorted→Net", "Net→lib"}
    assert all(e["faded"] for e in view["edges"])


def test_focus_follows_the_level():
    graph, notes = focus_graph()
    view = compute_view(graph, notes, level=2, domain="Networking")
    assert node_ids(view, faded=False) == {"Net", "Peer"}
    assert "Peer→domain:UI" in pairs(view) and "Net→Peer" in pairs(view)
    assert not edge_of(view, "Net→Peer")["faded"]
    # level 0 has no classes: the focus shows level 1
    assert compute_view(graph, notes, level=0, domain="Networking")["level"] == 1


def test_focus_on_unsorted_and_zoom_wins():
    graph, notes = focus_graph()
    view = compute_view(graph, notes, level=1, domain="Unsorted")
    assert node_ids(view, faded=False) == {"Loose"}
    assert "Loose→domain:Networking" in pairs(view)
    zoomed = compute_view(graph, notes, domain="Networking", zoom={"from": "Net", "to": None, "level": 1})
    assert zoomed["domain"] is None and "Net" in node_ids(zoomed)


def test_api_parameter():
    assert view_args({"domain": ["UI"]})["domain"] == "UI"
    assert view_args({})["domain"] is None


def test_text_command_round_trips():
    from coderstanding.textout import view_command, view_text
    graph, notes = focus_graph()
    text = view_text("t", graph, notes, level=1, domain="Networking")
    assert "--domain Networking" in text.splitlines()[0]
    assert "--domain" not in view_command("t", zoom={"from": "A", "to": None, "level": 1}, domain="X")
