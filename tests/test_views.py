from coderstanding.model import Call, Edge, Graph, Member, Node
from coderstanding.notes import Notes
from coderstanding.views import compute_view


def make_graph(nodes, edges):
    """nodes: {id: level} or {id: (level, kind, flags)}; edges: (from, to, kind[, calls])."""
    graph = Graph(repo="t")
    for node_id, spec in nodes.items():
        level, kind, flags = spec if isinstance(spec, tuple) else (spec, "class", [])
        graph.add_node(Node(id=node_id, name=node_id, kind=kind, lang="x",
                            file=f"{node_id}.x", level=level, flags=list(flags)))
    for from_id, to, kind, *calls in edges:
        members = calls[0] if calls else []
        graph.add_edge(Edge(from_id, to, kind, calls=[Call(m, 1) for m in members]))
    return graph


def pairs(view, faded=None):
    return {e["key"] for e in view["edges"] if faded is None or e["faded"] == faded}


def edge_of(view, key):
    return next(e for e in view["edges"] if e["key"] == key)


def node_ids(view, faded=None):
    return {n["id"] for n in view["nodes"] if faded is None or n["faded"] == faded}


# DESIGN 5 example: GameEngine owns Viewport owns Renderer; Renderer uses IEffectSink (VFX).
def engine_graph():
    return make_graph(
        {"GameEngine": 1, "Viewport": 2, "Renderer": 3, "IEffectSink": (2, "interface", []),
         "VFX": 1, "camera": (1, "external", [])},
        [("GameEngine", "Viewport", "composition", ["resize"]),
         ("Viewport", "Renderer", "composition", ["render_frame"]),
         ("Renderer", "IEffectSink", "dependency", ["push_effect"]),
         ("VFX", "IEffectSink", "realization"),
         ("camera", "Viewport", "signal", ["became_active"]),
         ("Viewport", "camera", "dependency", ["follow_target"])])


def test_folding_chain_into_owner():
    view = compute_view(engine_graph(), level=1)
    assert node_ids(view) == {"GameEngine", "VFX", "camera"}
    edge = edge_of(view, "GameEngine→VFX")
    assert not edge["direct"]
    assert edge["via"] == [["Viewport", "Renderer", "IEffectSink"]]
    assert edge["calls"] == [{"member": "push_effect", "from_member": None, "count": 1}]
    assert [[c["member"] for c in h["calls"]] for h in edge["hops"][0]] == [
        ["resize"], ["render_frame"], ["push_effect"], []]


def test_signal_into_hidden_class_stops_at_owner():
    view = compute_view(engine_graph(), level=1)
    assert "camera→GameEngine" in pairs(view)
    assert edge_of(view, "camera→GameEngine")["via"] == [["Viewport"]]
    assert "GameEngine→camera" in pairs(view)
    assert "camera→VFX" not in pairs(view)


def test_concrete_rewrites_interface_with_as():
    notes = Notes()
    notes.set("edge:Renderer→IEffectSink", "claude", {"hint": "pushes effect requests"})
    view = compute_view(engine_graph(), notes, level=3, view="concrete")
    assert "IEffectSink" not in node_ids(view)
    edge = edge_of(view, "Renderer→VFX")
    assert edge["direct"] and edge["as"] == "IEffectSink"
    assert edge["hint"] == "pushes effect requests" and edge["hint_by"] == "claude"
    assert not any("realization" in e["kinds"] for e in view["edges"])


def test_all_view_shows_interface_and_realization():
    view = compute_view(engine_graph(), level=3)
    assert "IEffectSink" in node_ids(view)
    assert edge_of(view, "VFX→IEffectSink")["kinds"] == ["realization"]
    assert edge_of(view, "Renderer→IEffectSink")["direct"]


def test_hidden_interface_folds_into_bound_impls_in_all_view():
    view = compute_view(engine_graph(), level=1)
    # IEffectSink (level 2) is hidden: the line lands on VFX through it.
    assert "IEffectSink" in edge_of(view, "GameEngine→VFX")["via"][0]


def test_bindings_from_notes_and_needs_binding():
    graph = make_graph({"A": 1, "I": (2, "interface", []), "X": 1, "Y": 1},
                       [("A", "I", "dependency"), ("X", "I", "realization"),
                        ("Y", "I", "realization")])
    view = compute_view(graph, level=1, view="concrete")
    assert pairs(view) == {"A→X", "A→Y"}
    assert view["needs_binding"] == [{"id": "I", "candidates": ["X", "Y"]}]
    notes = Notes()
    notes.set("node:I", "you", {"bindings": ["Y"]})
    view = compute_view(graph, notes, level=1, view="concrete")
    assert pairs(view) == {"A→Y"} and view["needs_binding"] == []


def test_owner_priority_composition_over_association():
    graph = make_graph({"Big": 1, "Other": 1, "Part": 3},
                       [("Other", "Part", "association"), ("Big", "Part", "composition"),
                        ("Part", "Other", "dependency", ["ping"])])
    view = compute_view(graph, level=1)
    assert "Big→Other" in pairs(view)
    assert "Other→Big" in pairs(view)  # Other's link into Part lands on Part's owner


def test_owner_tie_goes_to_higher_level():
    graph = make_graph({"Top": 1, "Mid": 2, "Part": 3, "Far": 1},
                       [("Mid", "Part", "composition"), ("Top", "Part", "composition"),
                        ("Top", "Mid", "composition"), ("Part", "Far", "dependency")])
    view = compute_view(graph, level=2)
    assert edge_of(view, "Top→Far")["via"] == [["Part"]]
    assert "Mid→Far" not in pairs(view)


def test_owner_hidden_too_keeps_going_up():
    graph = make_graph({"A": 1, "B": 3, "C": 4, "D": 1},
                       [("A", "B", "association"), ("B", "C", "dependency"),
                        ("C", "D", "dependency")])
    view = compute_view(graph, level=1)
    assert edge_of(view, "A→D")["via"] == [["B", "C"]]


def test_ownerless_hidden_class_forwards_incoming():
    graph = make_graph({"A": 1, "Hub": 3, "B": 1, "C": 1},
                       [("A", "Hub", "signal"), ("Hub", "B", "signal"), ("Hub", "C", "signal")])
    view = compute_view(graph, level=1)
    assert pairs(view) == {"A→B", "A→C"}
    assert edge_of(view, "A→B")["via"] == [["Hub"]]


def test_ownerless_hidden_source_alone_is_dropped():
    graph = make_graph({"Orphan": 3, "B": 1}, [("Orphan", "B", "dependency")])
    assert pairs(compute_view(graph, level=1)) == set()


def test_internal_edges_dropped():
    graph = make_graph({"A": 1, "B": 2, "C": 2},
                       [("A", "B", "composition"), ("A", "C", "composition"),
                        ("B", "C", "dependency")])
    assert pairs(compute_view(graph, level=1)) == set()


def test_struct_edges_only_when_both_visible():
    graph = make_graph({"A": 1, "Base": 2, "B": 1},
                       [("A", "Base", "generalization"), ("B", "Base", "composition")])
    assert pairs(compute_view(graph, level=1)) == set()
    assert "A→Base" in pairs(compute_view(graph, level=2))


def test_direct_and_derived_merge_into_one_edge():
    graph = make_graph({"A": 1, "Part": 2, "B": 1},
                       [("A", "B", "dependency", ["x"]), ("A", "Part", "composition"),
                        ("Part", "B", "signal", ["y"])])
    edge = edge_of(compute_view(graph, level=1), "A→B")
    assert edge["direct"] and edge["via"] == [["Part"]]
    assert edge["kinds"] == ["dependency", "signal"]
    assert {c["member"] for c in edge["calls"]} == {"x", "y"}


def test_cycle_of_hidden_owners_does_not_loop():
    graph = make_graph({"P": 3, "Q": 3, "A": 1},
                       [("P", "Q", "composition"), ("Q", "P", "composition"),
                        ("A", "P", "dependency")])
    compute_view(graph, level=1)  # must terminate


def test_note_level_overrides_scan():
    graph = make_graph({"A": 1, "B": 3}, [("A", "B", "dependency")])
    notes = Notes()
    notes.set("node:B", "claude", {"level": 1})
    view = compute_view(graph, notes, level=1)
    assert "A→B" in pairs(view)
    assert next(n for n in view["nodes"] if n["id"] == "B")["level"] == 1


def test_singleton_note_sets_flag():
    graph = make_graph({"A": 1}, [])
    notes = Notes()
    notes.set("node:A", "you", {"singleton": True})
    assert compute_view(graph, notes)["nodes"][0]["flags"] == ["singleton"]


def test_external_always_visible_and_toggle():
    graph = make_graph({"A": 1, "Deep": 4, "addon": (5, "external", [])},
                       [("A", "Deep", "composition"), ("Deep", "addon", "dependency")])
    assert "A→addon" in pairs(compute_view(graph, level=1))
    view = compute_view(graph, level=1, show={"external": False})
    assert "addon" not in node_ids(view) and pairs(view) == set()


def test_hiding_the_tscn_type_removes_scene_nodes():
    graph = make_graph({"main.tscn": (1, "scene", []), "Player": 1},
                       [("main.tscn", "Player", "composition")])
    graph.nodes["main.tscn"].file = "main.tscn"
    assert "main.tscn→Player" in pairs(compute_view(graph))
    view = compute_view(graph, show={"hide_types": [".tscn"]})
    assert node_ids(view) == {"Player"} and view["counts"]["total"] == 1


def test_pure_abstract_removed_in_concrete_but_real_code_stays():
    graph = make_graph({"Base": (1, "class", ["abstract"]), "Impl": 1, "User": 1},
                       [("Impl", "Base", "generalization"), ("User", "Base", "dependency")])
    view = compute_view(graph, view="concrete")
    assert "Base" not in node_ids(view)
    assert edge_of(view, "User→Impl")["as"] == "Base"
    graph.nodes["Base"].members.append(Member("hp", "field"))
    view = compute_view(graph, view="concrete")
    assert "Base" in node_ids(view) and "User→Base" in pairs(view)


def test_domains_level_zero():
    graph = make_graph({"Net": 1, "Peer": 2, "Ui": 1, "Loose": 1, "Deep": 3},
                       [("Net", "Peer", "composition"), ("Peer", "Ui", "signal", ["joined"]),
                        ("Ui", "Net", "dependency"), ("Loose", "Deep", "composition"),
                        ("Deep", "Net", "dependency")])
    notes = Notes()
    for cls, domain in (("Net", "Networking"), ("Peer", "Networking"), ("Ui", "UI")):
        notes.set(f"node:{cls}", "claude", {"domain": domain})
    view = compute_view(graph, notes, level=0)
    assert node_ids(view) == {"domain:Networking", "domain:UI", "Loose"}
    assert pairs(view) == {"domain:Networking→domain:UI", "domain:UI→domain:Networking",
                           "Loose→domain:Networking"}
    domain = next(n for n in view["nodes"] if n["id"] == "domain:UI")
    assert domain["kind"] == "domain" and domain["name"] == "UI"
    # the via path names the classes on both sides, so a zoom can show them
    assert edge_of(view, "domain:Networking→domain:UI")["via"] == [["Peer", "Ui"]]


def test_domain_zoom_shows_classes():
    graph = make_graph({"Net": 1, "Peer": 1, "Deep": 3, "Ui": 1},
                       [("Net", "Peer", "composition"), ("Peer", "Ui", "signal"),
                        ("Net", "Deep", "composition"), ("Deep", "Ui", "dependency")])
    notes = Notes()
    notes.set("node:Peer", "claude", {"domain": "Networking"})
    notes.set("node:Net", "claude", {"domain": "Networking"})
    notes.set("node:Ui", "claude", {"domain": "UI"})
    view = compute_view(graph, notes, zoom={"from": "domain:Networking", "to": "domain:UI",
                                            "level": 0})
    assert {"Peer", "Ui"} <= node_ids(view, faded=False)
    assert "Deep" not in node_ids(view)  # deeper than level 1 stays folded
    assert "Peer→Ui" in pairs(view)


def zoom_graph():
    return make_graph(
        {"A": 1, "M1": 2, "M2": 3, "B": 1, "C": 1, "Far": 1, "Side": 2},
        [("A", "M1", "composition"), ("M1", "M2", "composition"),
         ("M2", "B", "dependency", ["hit"]), ("M1", "C", "dependency"),
         ("C", "Far", "dependency"), ("Side", "Far", "signal")])


def test_zoom_shows_one_more_level():
    view = compute_view(zoom_graph(), zoom={"from": "A", "to": "B", "level": 1})
    assert view["level"] == 2 and view["zoom"]["via"] == ["M1", "M2"]
    assert node_ids(view, faded=False) == {"A", "B", "M1"}
    assert edge_of(view, "M1→B")["via"] == [["M2"]]
    assert not edge_of(view, "A→M1")["faded"]


def test_zoom_outside_only_if_linked():
    view = compute_view(zoom_graph(), zoom={"from": "A", "to": "B", "level": 1})
    assert node_ids(view, faded=True) == {"C"}
    assert edge_of(view, "M1→C")["faded"]
    assert "Far" not in node_ids(view) and "C→Far" not in pairs(view)


def test_zoom_with_given_via_drills_deeper():
    view = compute_view(zoom_graph(), zoom={"from": "M1", "to": "B", "level": 2,
                                            "via": ["M2"]})
    assert node_ids(view, faded=False) == {"M1", "M2", "B"}
    assert edge_of(view, "M2→B")["direct"]


def skip_graph():
    return make_graph(
        {"A": 1, "D1": 3, "D2": 4, "B": 1, "C": 1},
        [("A", "D1", "composition"), ("D1", "D2", "composition"),
         ("D2", "B", "dependency", ["hit"]), ("D1", "C", "dependency"),
         ("A", "C", "dependency")])


def test_zoom_skips_to_first_level_with_hidden_classes():
    view = compute_view(skip_graph(), zoom={"from": "A", "to": "B", "level": 1})
    assert view["level"] == 3 and view["zoom"]["level"] == 1
    assert node_ids(view, faded=False) == {"A", "B", "D1"}
    assert edge_of(view, "D1→B")["via"] == [["D2"]]  # deeper classes stay folded
    assert node_ids(view, faded=True) == set()
    assert "D1→C" not in pairs(view) and "A→C" not in pairs(view)
    assert pairs(view) == {"A→D1", "D1→B"}


def test_zoom_skip_drills_from_shown_level():
    view = compute_view(skip_graph(), zoom={"from": "D1", "to": "B", "level": 3})
    assert view["level"] == 4 and node_ids(view, faded=False) == {"D1", "D2", "B"}
    assert "D1→C" in pairs(view)  # a one-level zoom keeps its faded lines


def test_node_zoom_shows_neighbours():
    view = compute_view(zoom_graph(), zoom={"from": "C", "to": None, "level": 1})
    assert node_ids(view, faded=False) == {"C", "Far", "M1"}
    assert "C→Far" in pairs(view, faded=False)


def test_spec_focus_visible_class():
    view = compute_view(zoom_graph(), level=1, focus=["C"])
    assert node_ids(view, faded=False) == {"C"}
    assert node_ids(view, faded=True) == {"A", "Far"}
    assert all(e["faded"] for e in view["edges"])
    assert "A→B" not in pairs(view)


def test_spec_focus_hidden_class_lights_derived_edge():
    view = compute_view(engine_graph(), level=1, focus=["Renderer"])
    assert {"GameEngine", "VFX"} <= node_ids(view, faded=False)
    assert not edge_of(view, "GameEngine→VFX")["faded"]


def test_spec_focus_unknown_classes_leave_view_unchanged():
    plain = compute_view(engine_graph(), level=1)
    assert compute_view(engine_graph(), level=1, focus=["Nope"]) == plain


def test_output_shape_for_viewer():
    notes = Notes()
    notes.set("edge:GameEngine→VFX", "you", {"hint": "drives", "description": "long"})
    notes.set("node:VFX", "claude", {"description": "effects"})
    view = compute_view(engine_graph(), notes, level=1, view="all",
                        show={"external": True}, zoom=None, focus=None)
    assert set(view) >= {"level", "view", "zoom", "counts", "nodes", "edges"}
    assert view["level"] == 1 and view["view"] == "all" and view["zoom"] is None
    assert view["counts"] == {"shown": 3, "total": 6}
    node_keys = {"id", "name", "kind", "level", "flags", "file", "line", "faded", "description"}
    for node in view["nodes"]:
        assert set(node) >= node_keys and isinstance(node["flags"], list)
    vfx = next(n for n in view["nodes"] if n["id"] == "VFX")
    assert vfx["description"] == "effects" and vfx["faded"] is False
    edge_keys = {"key", "from", "to", "direct", "kinds", "calls", "via", "as", "hint",
                 "hint_by", "description", "faded"}
    for edge in view["edges"]:
        assert set(edge) >= edge_keys
        assert all(set(c) == {"member", "from_member", "count"} for c in edge["calls"])
    edge = edge_of(view, "GameEngine→VFX")
    assert (edge["hint"], edge["hint_by"], edge["description"]) == ("drives", "you", "long")
    assert edge["as"] is None
    import json
    json.dumps(view)  # JSON-serializable


# --- DESIGN 12a: tests hidden by default, node info ---

def graph_with_tests():
    graph = make_graph(
        {"Game": 1, "Net": 2, "TestNet": (1, "class", ["test"]), "Helper": (3, "class", ["test"])},
        [("Game", "Net", "composition", ["send"]), ("TestNet", "Net", "dependency", ["send"]),
         ("TestNet", "Helper", "composition")])
    graph.nodes["TestNet"].file = "tests/test_net.x"
    graph.nodes["Net"].lines, graph.nodes["Net"].span = 40, True
    graph.nodes["Net"].warnings = [{"kind": "cycle", "group": "Game", "size": 2}]
    return graph


def test_tests_hidden_by_default_and_removed_not_folded():
    view = compute_view(graph_with_tests(), level=3)
    assert node_ids(view) == {"Game", "Net"}
    assert pairs(view) == {"Game→Net"}
    assert view["counts"]["total"] == 2
    shown = compute_view(graph_with_tests(), level=3, show={"test": True})
    assert {"TestNet", "Helper"} <= node_ids(shown) and "TestNet→Net" in pairs(shown)


def test_node_json_has_tested_by_size_and_warnings():
    net = next(n for n in compute_view(graph_with_tests(), level=3)["nodes"] if n["id"] == "Net")
    assert net["tested_by"] == ["tests/test_net.x"]
    assert (net["lines"], net["members"], net["span"]) == (40, 0, True)
    assert net["warnings"] == [{"kind": "cycle", "group": "Game", "size": 2, "text": "cycle 2"}]


def test_node_json_has_status_tags_rules():
    notes = Notes()
    notes.set("node:Net", "claude", {"status": "wip", "tags": ["net"], "rules": "no blocking"})
    net = next(n for n in compute_view(graph_with_tests(), notes, level=3)["nodes"] if n["id"] == "Net")
    assert (net["status"], net["tags"], net["rules"], net["rules_by"]) == \
        ("wip", ["net"], "no blocking", "claude")


def test_node_json_function_field_counts():
    graph = graph_with_tests()
    graph.nodes["Net"].members = [Member("a", "method"), Member("b", "function"),
                                  Member("c", "field"), Member("d", "signal")]
    net = next(n for n in compute_view(graph, level=3)["nodes"] if n["id"] == "Net")
    assert (net["members"], net["functions"], net["fields"]) == (4, 2, 2)


def test_ignored_warnings_split_in_node_json():
    graph = graph_with_tests()
    graph.nodes["Net"].warnings.append({"kind": "unused"})
    notes = Notes()
    notes.set("node:Net", "you", {"ignore_warnings": ["cycle"]})
    net = next(n for n in compute_view(graph, notes, level=3)["nodes"] if n["id"] == "Net")
    assert [w["kind"] for w in net["warnings"]] == ["unused"]
    assert [w["text"] for w in net["ignored_warnings"]] == ["cycle 2"]
    assert net["ignore_warnings"] == ["cycle"]


def test_rule_ignored_warning_in_node_json_has_reason():
    graph = graph_with_tests()
    notes = Notes()
    notes.rules = [{"glob": "**", "ignore": ["cycle"]}]
    net = next(n for n in compute_view(graph, notes, level=3)["nodes"] if n["id"] == "Net")
    assert net["warnings"] == [] and net["ignored_warnings"][0]["reason"] == "rule ** cycle"
    assert net["ignore_warnings"] == []


def test_focus_hidden_connection_lights_its_derived_line():
    view = compute_view(engine_graph(), level=1, focus=["Renderer→IEffectSink"])
    assert view["focus"]["edges"] == ["GameEngine→VFX"]
    assert {"GameEngine", "VFX"} <= set(view["focus"]["nodes"])
    assert not edge_of(view, "GameEngine→VFX")["faded"]
