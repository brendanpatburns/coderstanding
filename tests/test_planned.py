"""Planned and added-manually items, stub functions and the one `is_built` rule (DESIGN 7f)."""

import pytest

from coderstanding.cli import main
from coderstanding.graph_json import member_to_json
from coderstanding.languages.cpp import CppModule
from coderstanding.languages.csharp import CSharpModule
from coderstanding.languages.gdscript import GDScriptModule
from coderstanding.languages.java import JavaModule
from coderstanding.languages.javascript import JavaScriptModule
from coderstanding.languages.kotlin import KotlinModule
from coderstanding.languages.python import PythonModule
from coderstanding.model import Edge, Graph, Member, Node
from coderstanding.notes import Notes, edge_key, function_key, load_notes, node_key
from coderstanding import planned
from coderstanding.planned import PLANNED, is_built
from coderstanding.scan import scan_repo
from coderstanding.textout import format_plan, view_text
from coderstanding.views import compute_view
from helpers import write_files


def stubs_of(tmp_path, files, module):
    write_files(tmp_path, files)
    graph = scan_repo(tmp_path, modules=[module])
    return {f"{n.id}#{m.name}": m.stub for n in graph.nodes.values() for m in n.members
            if m.kind in ("method", "function")}


# --- stub detection, one reader at a time ---

def test_python_stubs(tmp_path):
    found = stubs_of(tmp_path, {"a.py": (
        "import abc\n"
        "def empty():\n    pass\n"
        "def todo():\n    # TODO: write it\n    pass\n"
        "def on_purpose():\n    # nothing to do here\n    pass\n"
        "def docstring_only():\n    \"\"\"Does it.\"\"\"\n"
        "def ellipsis(): ...\n"
        "def raises():\n    \"\"\"Doc.\"\"\"\n    raise NotImplementedError('later')\n"
        "def real():\n    return 1\n"
        "class A:\n"
        "    def __init__(self):\n        pass\n"
        "    @abc.abstractmethod\n    def must(self):\n        raise NotImplementedError\n"
        "    def later(self):\n        raise NotImplementedError()\n")}, PythonModule())
    assert found == {"a#empty": True, "a#todo": True, "a#on_purpose": False, "a#docstring_only": True,
                     "a#ellipsis": True, "a#raises": True, "a#real": False,
                     "a.A#__init__": False, "a.A#must": False, "a.A#later": True}


def test_gdscript_stubs(tmp_path):
    found = stubs_of(tmp_path, {"p.gd": (
        "class_name P\nextends Node\n"
        "func _ready():\n\tpass\n"
        "func _init():\n\tpass\n"
        "func later():\n\tpush_error(\"not implemented\")\n"
        "func fixme():\n\t# FIXME\n\tpass\n"
        "func real():\n\tprint(1)\n")}, GDScriptModule())
    assert found == {"P#_ready": True, "P#_init": False, "P#later": True, "P#fixme": True, "P#real": False}


def test_csharp_java_kotlin_stubs(tmp_path):
    cs = stubs_of(tmp_path / "cs", {"A.cs": (
        "class A {\n  public A() {}\n  void Empty() {}\n  void Later() { throw new NotImplementedException(); }\n"
        "  int Arrow() => throw new System.NotImplementedException();\n  int Real() { return 1; }\n}\n")},
        CSharpModule())
    assert cs == {"A#A": False, "A#Empty": True, "A#Later": True, "A#Arrow": True, "A#Real": False}
    java = stubs_of(tmp_path / "java", {"B.java": (
        "class B {\n  B() {}\n  void later() { throw new UnsupportedOperationException(\"x\"); }\n"
        "  void todo() { // TODO\n  }\n  int real() { return 1; }\n}\n"
        "interface I { void none(); }\n")}, JavaModule())
    assert java == {"B#B": False, "B#later": True, "B#todo": True, "B#real": False, "I#none": False}
    kotlin = stubs_of(tmp_path / "kt", {"C.kt": (
        "class C {\n  fun later() { TODO() }\n  fun expr() = TODO(\"soon\")\n  fun real() = 1\n}\n")},
        KotlinModule())
    assert kotlin == {"C#later": True, "C#expr": True, "C#real": False}


def test_javascript_and_cpp_stubs(tmp_path):
    js = stubs_of(tmp_path / "js", {"a.js": (
        "export class A {\n  constructor() {}\n  later() { throw new Error('not implemented'); }\n"
        "  empty() {}\n  real() { return 1; }\n}\n"
        "export function todo() { /* TODO */ }\nexport const arrow = () => null;\n")}, JavaScriptModule())
    assert {k.rsplit("#", 1)[1]: v for k, v in js.items()} == {
        "constructor": False, "later": True, "empty": True, "real": False, "todo": True, "arrow": False}
    cpp = stubs_of(tmp_path / "cpp", {"d.cpp": (
        "void later() { throw std::logic_error(\"not implemented\"); }\nint real() { return 1; }\n"
        "class D { public: void empty() {} };\n")}, CppModule())
    assert {k.rsplit("#", 1)[1]: v for k, v in cpp.items()} == {"later": True, "real": False, "empty": True}


def test_stub_kept_in_graph_json_only_when_set():
    assert member_to_json(Member("f", "method", stub=True))["stub"] is True
    assert "stub" not in member_to_json(Member("f", "method"))
    assert "made" not in member_to_json(Member("f", "method", made=PLANNED))


# --- the built rule ---

def test_is_built():
    assert is_built(Member("f", "method"))
    assert not is_built(Member("f", "method", stub=True))
    assert not is_built(Member("f", "method", made=PLANNED))
    assert not is_built(Node("X", "X", "class", "", "", made=PLANNED))
    assert is_built(Node("X", "X", "class", "", "", made=planned.MANUAL))
    assert not is_built({"name": "f", "stub": True}) and is_built({"name": "f"})


# --- the view engine ---

def small_graph():
    graph = Graph(repo="t")
    graph.add_node(Node("app.Server", "Server", "class", "py", "app/server.py", level=1,
                        members=[Member("serve", "method", line=2, end=4),
                                 Member("later", "method", line=5, end=6, stub=True)]))
    graph.add_node(Node("app.Notes", "Notes", "class", "py", "app/notes.py", level=1))
    graph.add_edge(Edge("app.Server", "app.Notes", "dependency"))
    return graph


def planned_notes():
    notes = Notes()
    notes.set(node_key("node_store"), "you", {"made": PLANNED, "hint": "keeps the boxes", "stage": "todo",
                                              "priority": "high"}, ai=False)
    notes.set(node_key("hosted page"), "claude", {"made": "manual", "where": "deploy/hosted.json"})
    notes.set(edge_key("app.Server", "node_store"), "you", {"made": PLANNED, "hint": "saves here"}, ai=False)
    notes.set(function_key("app.Server", "plan_list"), "you", {"made": PLANNED, "hint": "lists them"}, ai=False)
    notes.set(function_key("node_store", "add"), "you", {"made": PLANNED}, ai=False)
    return notes


def test_view_adds_planned_and_manual_boxes_lines_and_rows():
    view = compute_view(small_graph(), planned_notes(), expand=["app.Server", "node_store"])
    nodes = {n["id"]: n for n in view["nodes"]}
    box = nodes["node_store"]
    assert (box["made"], box["made_label"], box["stage"], box["stage_from"], box["priority"]) == \
        (PLANNED, "Planned", "todo", "own", "high")
    assert nodes["hosted page"]["made"] == "manual" and nodes["hosted page"]["where"] == "deploy/hosted.json"
    assert nodes["hosted page"]["made_ai"] is True
    line = next(e for e in view["edges"] if e["to"] == "node_store")
    assert line["kinds"] == [PLANNED] and line["hint"] == "saves here"
    rows = {f["name"]: f for f in view["expanded"]["app.Server"]["functions"]}
    assert rows["later"]["stub"] is True and rows["plan_list"]["made"] == PLANNED
    assert rows["plan_list"]["hint"] == "lists them" and "made" not in rows["serve"]
    assert [f["name"] for f in view["expanded"]["node_store"]["functions"]] == ["add"]
    assert view["plan"] == {"planned": 1, "manual": 1, "functions": 2, "stubs": 1}
    hidden = compute_view(small_graph(), planned_notes(), show={"planned": False})
    assert {n["id"] for n in hidden["nodes"]} == {"app.Server", "app.Notes", "hosted page"}


def test_saved_graph_is_untouched():
    graph = small_graph()
    compute_view(graph, planned_notes(), expand=["app.Server"])
    assert "node_store" not in graph.nodes
    assert [m.name for m in graph.nodes["app.Server"].members] == ["serve", "later"]


def test_planned_function_built_now_links_by_name_and_keeps_notes():
    graph, notes = small_graph(), planned_notes()
    graph.nodes["app.Server"].members.append(Member("plan_list", "method", line=7, end=9))
    view = compute_view(graph, notes, expand=["app.Server"])
    row = next(f for f in view["expanded"]["app.Server"]["functions"] if f["name"] == "plan_list")
    assert "made" not in row and row["hint"] == "lists them"  # built: the real row has the notes
    listed = planned.plan_rows(graph, notes)
    assert next(r for r in listed["functions"] if r["name"] == "plan_list")["built"] is True


def test_close_function_name_is_offered_not_linked():
    graph, notes = small_graph(), Notes()
    graph.nodes["app.Server"].members.append(Member("planList", "method", line=7))
    notes.set(function_key("app.Server", "plan_list"), "you", {"made": PLANNED}, ai=False)
    row = planned.plan_rows(graph, notes)["functions"][0]
    assert row["built"] is False and row["matches"]["close"] == ["planList"]
    moved = planned.link_item(graph, notes, "app.Server#plan_list", "app.Server#planList")
    assert moved == [("node:app.Server#plan_list", "node:app.Server#planList")]
    assert notes.value(function_key("app.Server", "planList"), "made") is None


def test_spec_stage_shows_when_the_item_has_none(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "boxes.md").write_text("---\nid: boxes\ntitle: Boxes\nstage: review\n---\n")
    notes = Notes()
    notes.data_dir = tmp_path
    notes.set(node_key("node_store"), "you", {"made": PLANNED, "spec": "boxes"}, ai=False)
    box = next(n for n in compute_view(small_graph(), notes)["nodes"] if n["id"] == "node_store")
    assert (box["stage"], box["stage_from"], box["spec"]) == ("review", "spec", "boxes")


def test_text_marks_planned_and_stubs():
    graph, notes = small_graph(), planned_notes()
    text = view_text("t", graph, notes, level=1, expand=["app.Server"])
    assert "Server ..> node_store [planned]: saves here" in text
    assert "later [stub], plan_list [planned idea]" in text  # the `no calls:` line of the open class
    listing = format_plan("t", planned.plan_rows(graph, notes))
    assert "node_store [planned] [todo] work:high" in listing
    assert "hosted page [manual] (where: deploy/hosted.json)" in listing
    assert "app.Server#later [stub] app/server.py:5" in listing


# --- writes ---

def test_add_asks_first_on_a_matching_name_and_refuses_bad_ones():
    graph, notes = small_graph(), Notes()
    assert planned.add_item(graph, notes, "Notes", "you", False)["ask"]["exact"] == ["app.Notes"]
    assert planned.add_item(graph, notes, "Note", "you", False)["ask"] == {"exact": [], "close": ["app.Notes"]}
    assert not notes.keys()  # nothing written while asking
    assert planned.add_item(graph, notes, "Notes", "you", False, anyway=True) == {"key": "node:Notes"}
    assert planned.add_item(graph, notes, "serve", "you", False, on="app.Server")["ask"]["exact"] == ["serve"]
    with pytest.raises(planned.PlanError):
        planned.add_item(graph, notes, "a#b", "you", False)
    with pytest.raises(planned.PlanError):
        planned.add_item(graph, notes, "Notes", "you", False, anyway=True)  # already there
    with pytest.raises(ValueError):
        notes.set(node_key("x"), "you", {"made": PLANNED, "stage": "soon"})


def test_built_now_class_links_into_the_real_one():
    graph, notes = small_graph(), planned_notes()
    graph.add_node(Node("app.node_store", "node_store", "class", "py", "app/node_store.py", level=1))
    box = next(n for n in compute_view(graph, notes)["nodes"] if n["id"] == "node_store")
    assert box["matches"]["exact"] == ["app.node_store"]
    planned.link_item(graph, notes, "node_store", "app.node_store")
    assert notes.value(node_key("app.node_store"), "hint") == "keeps the boxes"
    assert notes.value(node_key("app.node_store"), "made") is None
    assert notes.value(node_key("app.node_store"), "stage") is None
    assert notes.value(edge_key("app.Server", "app.node_store"), "made") == PLANNED  # not in the code yet
    assert notes.value(function_key("app.node_store", "add"), "made") == PLANNED


def test_drop_takes_lines_and_accept_makes_it_yours():
    notes = planned_notes()
    dropped = planned.drop_item(notes, node_key("node_store"))
    assert set(dropped) == {"node:node_store", "node:node_store#add", "edge:app.Server→node_store"}
    planned.accept_item(notes, node_key("hosted page"), "you")
    assert notes.get(node_key("hosted page"), "made") == ("manual", "you")
    assert not notes.is_ai(node_key("hosted page"), "you")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    write_files(repo, {"server.py": "class Server:\n    def serve(self):\n        return 1\n"
                                    "    def later(self):\n        pass\n",
                       "notes.py": "class Notes:\n    pass\n"})
    main(["add", "t", str(repo)])
    return repo


def test_cli_plan_add_list_link(app, capsys):
    main(["plan", "t", "--add", "node_store", "--note", "keeps the boxes", "--stage", "todo",
          "--links", "Server>node_store"])
    assert "added [planned] node_store (AI draft)" in capsys.readouterr().out
    main(["plan", "t", "--add", "plan_list", "--on", "Server", "--by", "you"])
    with pytest.raises(SystemExit) as stop:
        main(["plan", "t", "--add", "Notes"])
    assert "is real code: notes.Notes" in str(stop.value.code)
    capsys.readouterr()
    main(["plan", "t"])
    out = capsys.readouterr().out
    assert "node_store [planned] [todo] (AI draft)" in out and "server.Server#plan_list [planned idea]" in out
    assert "server.Server#later [stub] server.py:4" in out and "server.Server ..> node_store [planned]" in out
    main(["node", "t", "node_store"])
    assert "NOT IN CODE" in capsys.readouterr().out
    main(["set", "t", "node_store", "priority=high"])
    main(["plan", "t", "--drop", "node_store"])
    assert "dropped" in capsys.readouterr().out
    notes = load_notes(app / ".coderstanding" / "notes.jsonl")
    assert notes.value(node_key("node_store"), "made") is None


def test_server_plan_routes(tmp_path):
    from coderstanding.apps import App, rescan
    from coderstanding.server import Api
    from test_tours_api import call, serve
    write_files(tmp_path / "repo", {"m.py": "class Player:\n    def hit(self):\n        pass\n"})
    app = App("app", tmp_path / "repo", tmp_path / "repo" / ".coderstanding")
    srv, base = serve(Api(app, graph=rescan(app)))
    try:
        asked = call(base, "/api/plan?app=app", {"name": "player"}, "POST")
        assert asked["ask"] == {"exact": [], "close": ["m.Player"]}
        added = call(base, "/api/plan?app=app", {"name": "player", "anyway": True, "fields": {"stage": "todo"},
                                                  "links": [["m.Player", "player"]]}, "POST")
        assert added == {"key": "node:player"}
        call(base, "/api/plan?app=app", {"name": "heal", "on": "m.Player"}, "POST")
        rows = call(base, "/api/plan?app=app")
        assert [r["id"] for r in rows["classes"]] == ["player"] and rows["stubs"][0]["name"] == "hit"
        view = call(base, "/api/view?app=app&level=5&expand=m.Player")
        assert {n["id"]: n.get("made") for n in view["nodes"]}["player"] == "planned"
        rows = {f["name"]: f for f in view["expanded"]["m.Player"]["functions"]}
        assert rows["hit"]["stub"] and rows["heal"]["made"] == "planned"
        assert call(base, "/api/view?app=app&planned=0")["plan"]["planned"] == 0
        put = call(base, "/api/notes?app=app", {"key": "node:player", "fields": {"priority": "high"}}, "PUT")
        assert put["key"] == "node:player"
        assert call(base, "/api/plan/drop?app=app", {"key": "node:player"}, "POST")["dropped"] == [
            "edge:m.Player→player", "node:player"]
    finally:
        srv.shutdown()
        srv.server_close()
