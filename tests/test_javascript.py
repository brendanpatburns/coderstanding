from pathlib import Path

from coderstanding.languages.javascript import JavaScriptModule
from coderstanding.scan import scan_repo

FIXTURE = Path(__file__).parent / "fixtures" / "ts_basic"
CANVAS = "src/canvas.ts.Canvas"


def scan():
    return scan_repo(FIXTURE, modules=[JavaScriptModule()])


def edges(graph, kind):
    return {(f, t) for (f, t, k) in graph.edges if k == kind}


def test_module_nodes_and_classes():
    graph = scan()
    assert graph.nodes["src/canvas.ts"].kind == "module"
    assert graph.nodes[CANVAS].kind == "class"
    assert graph.nodes["src/shape.ts.Drawable"].kind == "interface"
    assert any(m.name == "area" for m in graph.nodes["src/geometry.ts"].members)


def test_extends_and_implements():
    graph = scan()
    assert (CANVAS, "src/shape.ts.Shape") in edges(graph, "generalization")
    assert (CANVAS, "src/shape.ts.Drawable") in edges(graph, "realization")


def test_composition_and_association_with_field_calls():
    graph = scan()
    assert (CANVAS, "src/geometry.ts.Point") in edges(graph, "composition")
    held = graph.edges[(CANVAS, "src/geometry.ts.Point", "composition")]
    assert [c.member for c in held.calls] == ["moveBy"]
    assert ("web/app.js.App", "src/canvas.ts.Canvas") in edges(graph, "composition")
    held = graph.edges[("web/app.js.App", "src/canvas.ts.Canvas", "composition")]
    assert [c.member for c in held.calls] == ["draw"]


def test_typed_param_stored_is_association():
    assert (CANVAS, "src/geometry.ts.Point") not in edges(scan(), "association")


def test_imports_are_dependencies_with_import_detail():
    graph = scan()
    deps = edges(graph, "dependency")
    assert ("src/canvas.ts", "src/shape.ts") in deps
    assert ("web/app.js", "web/util.js") in deps
    assert ("web/app.js", "src/canvas.ts") in deps
    assert graph.edges[("src/canvas.ts", "src/shape.ts", "dependency")].detail == ["import"]


def test_namespace_and_named_calls_count_on_module():
    graph = scan()
    area = graph.edges[(CANVAS, "src/geometry.ts", "dependency")]
    assert [c.member for c in area.calls] == ["area"]
    calls = graph.edges[("web/app.js.App", "web/util.js", "dependency")].calls
    assert sorted(c.member for c in calls) == ["greet", "shout"]


def test_new_not_stored_is_dependency():
    edge = scan().edges[(CANVAS, "src/geometry.ts.Point", "dependency")]
    assert "create" in edge.detail


def test_event_with_known_target_is_signal():
    graph = scan()
    signal = graph.edges[(CANVAS, "src/bus.ts", "signal")]
    assert signal.detail == ["resize"] and signal.auto == "resize -> this.redraw"
    assert ("web/app.js.App", "web/util.js") in edges(graph, "signal")


def test_unknown_event_sources_and_npm_packages():
    graph = scan()
    assert not any(k == "signal" and f == "web/app.js.App" and t != "web/util.js"
                   for (f, t, k) in graph.edges)
    assert ("web/app.js", "lodash", "dependency") in graph.nodes["web/app.js"].external_refs
    assert ("src/canvas.ts", "events", "dependency") in graph.nodes["src/canvas.ts"].external_refs


def test_from_member(tmp_path):
    from coderstanding.language import FileContext
    (tmp_path / "u.ts").write_text("export function go() {}\nexport class Foo {}\n")
    (tmp_path / "a.ts").write_text("""import { go, Foo } from './u';
go();
export class A extends Foo {
  held: Foo;
  tick = () => { go(); };
  run(p: Foo) { items.forEach(() => go()); }
}
const helper = () => { go(); };
""")
    result = JavaScriptModule().parse(FileContext(tmp_path, "a.ts"))
    found = [(r.detail, r.call, r.from_member) for r in result.refs
             if r.detail != "import" and r.kind != "generalization"]
    assert ("field", "", "<field>") in found
    assert ("param", "", "run") in found
    assert ("call", "go", "<init>") in found
    assert ("call", "go", "tick") in found  # class field arrow function
    assert ("call", "go", "run") in found  # anonymous callback: its enclosing method
    assert ("call", "go", "helper") in found
    assert [r.from_member for r in result.refs if r.kind == "generalization"] == [None]
