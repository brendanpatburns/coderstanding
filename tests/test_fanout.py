"""Function fan-out (DESIGN 12c): lines re-attached to the functions of expanded classes."""

from coderstanding.notes import Notes
from coderstanding.views import compute_view
from helpers import fan_graph


def links(view):
    return {l["key"]: l for l in view["fanout"]}


def view_of(*expand, notes=None):
    return compute_view(fan_graph(), notes, level=1, expand=list(expand))


def test_nothing_expanded_adds_nothing():
    view = view_of()
    assert view["fanout"] == [] and view["expanded"] == {}
    assert not any(e["fanned"] for e in view["edges"])


def test_expanded_source_starts_at_calling_function():
    view = view_of("Player")
    found = links(view)
    take_hit = found["Player#take_hit→VFX"]
    assert take_hit["members"] == [{"name": "push_effect", "count": 2}]
    assert take_hit["count"] == 2 and take_hit["at"] == [{"file": "x.x", "line": 10}]
    assert take_hit["kinds"] == ["association"] and take_hit["direct"]
    assert found["Player#die→VFX"]["members"][0]["name"] == "clear_effects"
    assert found["Player→VFX"]["kinds"] == ["composition"]  # the field stays on the class
    assert {e["key"]: e["fanned"] for e in view["edges"]}["Player→VFX"]


def test_expanded_target_ends_at_called_function():
    found = links(view_of("VFX"))
    assert found["Player→VFX#push_effect"]["count"] == 2
    assert "Player→VFX#clear_effects" in found
    assert found["Audio→VFX#push_effect"]["count"] == 1


def test_both_expanded_and_init_row():
    view = view_of("Player", "VFX", "Audio")
    found = links(view)
    assert "Player#take_hit→VFX#push_effect" in found
    assert "Audio#<init>→VFX#push_effect" in found
    assert [f["name"] for f in view["expanded"]["Audio"]["functions"]] == ["play_sfx", "<init>"]
    assert view["expanded"]["Player"]["fields"] == [{"name": "vfx", "kind": "field", "type": "VFX"}]


def test_call_without_function_stays_on_class():
    found = links(view_of("Player", "Audio"))
    assert found["Player→Audio"]["members"] == [{"name": "volume", "count": 1}]
    assert found["Player#play_step→Audio#play_sfx"]["count"] == 3


def test_internal_calls_only_inside_expanded_class():
    assert not any(l["internal"] for l in view_of("VFX")["fanout"])
    inner = links(view_of("Player"))["Player#_physics_process→Player#play_step"]
    assert inner["internal"] and inner["edge"] is None


def test_derived_edge_fans_out_with_first_and_last_hop():
    view = view_of("Game", "VFX")
    assert {e["key"]: e["direct"] for e in view["edges"]}["Game→VFX"] is False
    link = links(view)["Game#_ready→VFX#spawn_sparks"]
    assert not link["direct"] and link["members"] == [{"name": "spawn_sparks", "count": 1}]
    assert "Game→VFX#spawn_sparks" in links(view_of("VFX"))  # source collapsed


def test_expand_ignores_hidden_and_star_expands_all():
    assert view_of("Starter")["expanded"] == {}  # hidden at level 1
    assert set(view_of("*")["expanded"]) == {"Player", "VFX", "Audio", "Game"}


def test_function_notes_in_expanded():
    notes = Notes()
    notes.set("node:Player#take_hit", "claude", {"hint": "loses health"})
    rows = view_of("Player", notes=notes)["expanded"]["Player"]["functions"]
    take_hit = next(f for f in rows if f["name"] == "take_hit")
    assert (take_hit["hint"], take_hit["hint_by"], take_hit["hint_ai"]) == \
        ("loses health", "claude", True)
    assert take_hit["line"] == 3


def test_scanned_self_calls_show_inside_an_open_class(tmp_path):
    from helpers import scan_fake
    graph = scan_fake(tmp_path, {
        "a.fk": "type A class entry\nref dependency A call helper\nref association B call go\n",
        "b.fk": "type B class\n"})
    view = compute_view(graph, None, level=2, expand=["A"])
    assert [e["key"] for e in view["edges"]] == ["A→B"]  # no self line on the graph
    internal = [l for l in view["fanout"] if l["internal"]]
    assert [m["name"] for l in internal for m in l["members"]] == ["helper"]


def test_mockup_fixture_scans_into_the_mockup_lines():
    """tests/fixtures/fanout_mock is the approved mock-up's sample, used for viewer checks."""
    from pathlib import Path
    from coderstanding.scan import scan_repo
    graph = scan_repo(Path(__file__).parent / "fixtures" / "fanout_mock")
    view = compute_view(graph, None, level=9, view="concrete", expand=["Player", "VFX"])
    pairs = {(l["from"], l["from_fn"], l["to"], l["to_fn"]) for l in view["fanout"]}
    bus = "res://audio_bus.gd"
    assert {("Player", "take_hit", "VFX", "push_effect"), ("Player", "die", "VFX", "clear_effects"),
            ("Player", "play_step", bus, None), ("Player", "take_hit", bus, None),
            ("VFX", "spawn_sparks", "ParticlePool", None), ("VFX", "_on_effect_done", "ParticlePool", None),
            ("Player", "_physics_process", "Player", "play_step")} <= pairs
