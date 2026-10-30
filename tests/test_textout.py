from coderstanding import textout
from coderstanding.views import compute_view
from helpers import fan_graph


def node(node_id, kind="class", flags=(), name=None, dep=None, file=None, faded=False):
    return {"id": node_id, "name": name or node_id, "kind": kind, "level": 1,
            "flags": list(flags), "file": file or f"src/{node_id}.gd", "line": 1,
            "faded": faded, "domain": None, "dep": dep, "hint": None, "description": None}


def edge(a, b, direct=True, kinds=("dependency",), calls=(), via=(), as_=None, hint=None,
         faded=False):
    return {"key": f"{a}→{b}", "from": a, "to": b, "direct": direct, "kinds": list(kinds),
            "calls": [{"member": c, "count": 1} for c in calls], "via": [list(p) for p in via],
            "as": as_, "hint": hint, "hint_by": None, "description": None, "faded": faded}


def view(nodes, edges, zoom=None, level=1):
    return {"level": level, "view": "concrete", "zoom": zoom,
            "counts": {"shown": len([n for n in nodes if not n["faded"]]), "total": 10},
            "nodes": nodes, "edges": edges}


def test_graph_lines():
    nodes = [node("GameEngine", flags=["singleton"]), node("VFX"),
             node("cam", kind="external", dep="addon"), node("IFoo", kind="interface"),
             node("hud", kind="scene"), node("Lonely")]
    edges = [edge("GameEngine", "VFX", direct=False, via=[["V", "R", "I"], ["V"]],
                  hint="drives rendering", calls=["a", "b", "c", "d", "e"]),
             edge("VFX", "cam", kinds=["signal", "dependency"]),
             edge("hud", "IFoo", as_="IBar", hint="asks")]
    text = textout.format_graph("mygame", view(nodes, edges))
    assert text.splitlines() == [
        "mygame L1 concrete 6/10",
        "GameEngine* ~> VFX (via 1): drives rendering {a, b, c +2}",
        "VFX > cam [addon]: [signal, dependency]",
        "hud [scene] > IFoo [I]: as IBar: asks",
        "alone: Lonely",
    ]


def test_all_calls():
    text = textout.format_graph("w", view([node("A"), node("B")],
                                          [edge("A", "B", calls=["a", "b", "c", "d"])]), True)
    assert text.splitlines()[1] == "A > B: [dependency] {a, b, c, d}"


def test_name_clash_uses_file_name_then_id():
    nodes = [node("res://a/main.gd", name="main", file="a/main.gd"),
             node("res://main.tscn", name="main", kind="scene", file="main.tscn")]
    names = textout.labels(view(nodes, []))
    assert names == {"res://a/main.gd": "main.gd", "res://main.tscn": "main.tscn [scene]"}


def test_zoom_has_out_line():
    nodes = [node("A"), node("B"), node("M"), node("C", faded=True)]
    edges = [edge("A", "M", kinds=["composition"]), edge("M", "B"),
             edge("M", "C", direct=False, via=[["X"]], faded=True)]
    text = textout.format_zoom("w", view(nodes, edges, zoom={"from": "A", "to": "B"}, level=2))
    assert text.splitlines() == ["w zoom A>B L2 concrete", "A > M: [composition]",
                                 "M > B: [dependency]", "out: M~>C"]


def test_zoom_prints_edge_description_only_when_present_and_truncated():
    nodes = [node("A"), node("B")]
    long = edge("A", "B")
    long["description"] = "First line.\nSecond " + "x" * 300
    text = textout.format_zoom("w", view(nodes, [long], zoom={"from": "A", "to": "B"}, level=2))
    lines = text.splitlines()
    assert lines[1] == "A > B: [dependency]"
    assert lines[2].startswith("  First line. Second x") and len(lines[2]) == 2 + 200
    assert lines[2].endswith("…")
    plain = textout.format_zoom("w", view(nodes, [edge("A", "B")], zoom={"from": "A", "to": "B"}))
    assert len(plain.splitlines()) == 2


def test_graph_overview_has_no_descriptions():
    described = edge("A", "B")
    described["description"] = "Long text."
    text = textout.format_graph("w", view([node("A"), node("B")], [described]))
    assert "Long text." not in text


def test_node_detail():
    a = node("A")
    a.update(domain="Net", hint="talks", description="Longer words.")
    text = textout.format_node("w", view([a, node("B")], [edge("A", "B")]), "A")
    assert text.splitlines() == ["w node A L1 concrete", "[class] src/A.gd:1 L1 domain=Net: talks",
                                 "Longer words.", "A > B: [dependency]"]


def test_node_detail_size_marks_warnings_tests_rules():
    a = node("A")
    a.update(lines=120, members=8, span=True, status="wip", tags=["net", "core"], hint="talks",
             warnings=[{"kind": "god", "text": "god 40"}, {"kind": "cycle", "text": "cycle 3"}],
             tested_by=["tests/a_test.gd"], rules="no blocking\n\nkeep it small")
    lines = textout.node_detail(a)
    assert lines == ["[class] src/A.gd:1 L1 120 lines, 8 members [wip] #net #core: talks",
                     "warn: god 40, cycle 3", "tests: tests/a_test.gd",
                     "rules: no blocking", "  keep it small"]
    b = node("B")
    b.update(lines=30, members=0, span=False)
    assert textout.node_detail(b) == ["[class] src/B.gd:1 L1 30 lines (file)"]


def test_node_detail_review_is_one_short_line():
    a = node("A")
    a.update(review="split X into Y\nthen " + "z" * 300)
    line = textout.node_detail(a)[-1]
    assert line.startswith("review: split X into Y then z") and "\n" not in line
    assert len(line) <= len("review: ") + textout.DESCRIPTION_MAX


def test_zoom_prints_rules_of_both_ends_once():
    a, b = node("A"), node("B")
    a["rules"], b["rules"] = "never X", "always Y"
    text = textout.format_zoom("w", view([a, b], [edge("A", "B")], zoom={"from": "A", "to": "B"}))
    assert text.splitlines()[1:3] == ["rules A: never X", "rules B: always Y"]


def test_format_warnings():
    groups = {"unused": [("A", {"kind": "unused"})],
              "god": [("H", {"kind": "god", "degree": 40, "threshold": 20})],
              "cycle": [(i, {"kind": "cycle", "group": "C1", "size": 2}) for i in ("Y", "X")],
              "unsure": [], "unresolved": [("A", {"kind": "unresolved", "count": 3})]}
    assert textout.format_warnings("w", groups).splitlines() == [
        "w warnings: unused 1, god 1, cycle 2, unsure 0, unresolved 1",
        "unused: A", "god (>= 20): H 40", "cycle 2: X Y", "unresolved: A 3"]


# --- functions of expanded classes (DESIGN 12c) ---

def fan_text(**kwargs):
    return textout.view_text("t", fan_graph(), None, level=1, **kwargs)


def test_functions_lines_group_calls_and_list_callers():
    view = compute_view(fan_graph(), level=1, zoom={"from": "Player", "to": None, "level": 1},
                        expand=["*"])
    lines = textout.format_functions("t", view, "Player").splitlines()
    assert lines[0] == "t node Player L2 all functions"
    assert "Player#take_hit > VFX: push_effect ×2" in lines
    assert "Player#play_step > Audio: play_sfx ×3" in lines
    assert "  in: Player#_physics_process" in lines  # the internal call
    assert "Player (fields) > VFX: [composition]" in lines
    assert "Player (fields) > Audio: [association] volume" in lines


def test_view_text_command_expand_and_function_selection():
    lines = fan_text(expand=["VFX", "Player"], select="VFX#push_effect").splitlines()
    assert lines[0] == ("$ coderstanding view t --level 1 --expand VFX --expand Player "
                        "--select 'VFX#push_effect'")
    assert lines[1] == "t L1 all 4/5"
    assert "VFX#push_effect in: Audio, Player#take_hit ×2" in lines
    assert "VFX#spawn_sparks in: Game" in lines  # derived line, first hop from a collapsed end
    assert lines[lines.index("selected VFX#push_effect:") + 1] == \
        "VFX#push_effect in: Audio, Player#take_hit ×2"


def test_view_command_zoom_and_flags():
    command = textout.view_command("mygame", 2, True, {"test": True, "scene": False},
                                   {"from": "main.gd", "to": "a b", "level": 3}, ["*"], "a b→x")
    assert command == ("$ coderstanding view mygame --level 2 --concrete --zoom main.gd 'a b' "
                       "--zoom-level 3 --expand '*' --select 'a b→x' --tests --no-scenes")


def test_node_detail_tests_capped_at_eight():
    a = node("A")
    a["tested_by"] = [f"t{i}.gd" for i in range(10)]
    assert textout.node_detail(a)[-1] == "tests: " + ", ".join(f"t{i}.gd" for i in range(8)) + " +2 more"
    a["tested_by"] = a["tested_by"][:8]
    assert not textout.node_detail(a)[-1].endswith("more")


def test_format_warnings_marks_ignored():
    groups = {"unused": [("A", {"kind": "unused", "reason": "note", "ignored": True})],
              "god": [("H", {"kind": "god", "degree": 40, "threshold": 20, "reason": "note", "ignored": True})],
              "cycle": [("X", {"kind": "cycle", "group": "C", "size": 2, "reason": "note", "ignored": True}),
                        ("Y", {"kind": "cycle", "group": "C", "size": 2})],
              "unsure": [], "unresolved": [("A", {"kind": "unresolved", "count": 3, "reason": "note", "ignored": True})]}
    assert textout.format_warnings("w", groups).splitlines()[1:] == [
        "unused: A (ignored: note)", "god (>= 20): H (ignored: note) 40", "cycle 2: X (ignored: note) Y",
        "unresolved: A (ignored: note) 3"]


def test_view_command_prints_via_per_hidden_class():
    command = textout.view_command("w", 1, False, None,
                                   {"from": "A", "to": "B", "level": 1, "via": ["M", "N O"]})
    assert command == "$ coderstanding view w --level 1 --zoom A B --via M --via 'N O'"
    assert "--via" not in textout.view_command("w", 1, zoom={"from": "A", "to": "B", "via": []})
