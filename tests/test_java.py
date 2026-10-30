from pathlib import Path

from coderstanding.cli import main
from coderstanding.languages.java import JavaModule
from coderstanding.scan import scan_repo
from helpers import calls, edge, write_files

FIXTURE = Path(__file__).parent / "fixtures" / "java_sample"
M = "com.shop.model"
S = "com.shop.service"


def scan(root=FIXTURE):
    return scan_repo(root, modules=[JavaModule()])


def test_declarations_kinds_and_flags():
    nodes = scan().nodes
    assert nodes[f"{M}.Item"].kind == "class" and "abstract" in nodes[f"{M}.Item"].flags
    assert nodes[f"{S}.Pricing"].kind == "interface"
    assert nodes[f"{M}.Kind"].kind == "enum"
    assert nodes[f"{M}.Money"].kind == "record"
    assert nodes[f"{M}.Book.Author"].kind == "inner"
    assert nodes[f"{M}.Book.Page"].kind == "inner"
    assert nodes[f"{M}.Item"].lang == "java"
    assert nodes[f"{M}.Item"].line == 4 and nodes[f"{M}.Item"].lines == 4


def test_members_have_end_lines():
    cart = {m.name: m for m in scan().nodes[f"{S}.Cart"].members}
    assert cart["audit"].kind == "field" and cart["items"].type == "Map<String, Item>"
    assert cart["Cart"].kind == "method" and cart["Cart"].end > cart["Cart"].line
    assert cart["total"].line == 24 and cart["total"].end == 29
    assert {m.name for m in scan().nodes[f"{M}.Money"].members} == {"cents", "kind"}


def test_inheritance_edges():
    graph = scan()
    edge(graph, f"{M}.Book", f"{M}.Item", "generalization")
    edge(graph, f"{S}.Cart", f"{S}.Pricing", "realization")


def test_fields_composition_vs_association_and_calls():
    graph = scan()
    cart = f"{S}.Cart"
    edge(graph, cart, f"{M}.Money", "composition")  # new Money(..) initializer
    edge(graph, cart, f"{S}.Audit", "association")  # constructor-injected, assigned from a parameter
    assert calls(edge(graph, cart, f"{S}.Audit", "association")) == {"record": 1}
    notifier = edge(graph, cart, f"{S}.Notifier", "association")  # field injection
    assert calls(notifier) == {"send": 1}
    edge(graph, f"{M}.Book", f"{M}.Book.Author", "composition")  # generic argument of a held List


def test_import_scopes_resolve_across_packages():
    graph = scan()
    cart = f"{S}.Cart"
    edge(graph, cart, f"{M}.Item", "dependency")  # on-demand import com.shop.model.*, param type
    edge(graph, cart, f"{M}.Book", "dependency")  # generic argument of a return type
    edge(graph, f"{M}.Money", f"{M}.Kind", "association")  # same package, no import
    edge(graph, "com.shop.app.Main", f"{S}.Cart", "dependency")  # single-type import, new Cart()
    edge(graph, "com.shop.app.Main", f"{S}.Audit", "dependency")


def test_static_calls_and_static_import():
    graph = scan()
    util = edge(graph, f"{S}.Cart", f"{S}.Util", "dependency")
    assert set(calls(util)) >= {"log", "round"}  # Util.log() and the statically imported round()
    assert "newMap" in calls(util)  # field initializer call


def test_from_member_on_every_ref_but_type_headers():
    from coderstanding.language import FileContext
    refs = JavaModule().parse(FileContext(FIXTURE / "src/main/java/com/shop", "service/Cart.java")).refs
    headers = [r for r in refs if r.from_member is None]  # base types and class annotations
    assert {(r.kind, r.to.name) for r in headers} == {("generalization", "Pricing"), ("dependency", "Service")}
    by = {(r.to.name, r.detail): r.from_member for r in refs}
    assert by[("Audit", "field")] == "<field>" and by[("Audit", "param")] == "Cart"
    assert by[("Item", "param")] == "total" and by[("Autowired", "annotation")] == "<field>"
    assert {r.from_member for r in refs if r.to.name == "Util"} == {"<init>", "total"}


def test_main_is_a_program_entry():
    main = scan().nodes["com.shop.app.Main"]
    assert "program-main" in main.flags and "entry" in main.flags
    assert "entry" not in scan().nodes[f"{S}.Cart"].flags


def test_references_to_kotlin_stay_external_without_the_kotlin_reader():
    graph = scan()
    assert "com.x.KotlinThing" in graph.nodes[f"{S}.Cart"].external_refs or \
        any(e.to == "com.x.KotlinThing" for e in graph.nodes[f"{S}.Cart"].external_refs.values())


def test_maven_stack():
    stack = scan().stack
    project = stack["maven"][0]
    assert project["name"] == "Shop App" and project["group"] == "com.shop"
    assert project["version"] == "1.2.0" and project["packaging"] == "jar"
    deps = {d["name"]: d for d in stack["deps"] if d["kind"] == "maven"}
    assert deps["org.springframework:spring-context"]["version"] == "6.1.0"  # ${property}
    assert deps["com.google.guava:guava"]["version"] == "33.0.0"  # dependencyManagement
    assert deps["org.junit.jupiter:junit-jupiter"]["scope"] == "test"
    spring = deps["org.springframework:spring-context"]
    assert spring["used_by"] == [f"{S}.Cart"]  # @Service import maps to the dependency by groupId
    assert "java.util.Map" not in dict(stack["unresolved"]["top"])  # the JDK is no dependency


def test_nested_and_generics_edge_cases(tmp_path):
    write_files(tmp_path, {"p/A.java": """package p;
import java.util.*;
public class A<T extends Base> {
    private Map<String, List<Foo[]>> m;
    <U extends Bar> U get(Class<? extends Baz> c, T t) { return t.value(); }
    interface Inner { class Deep {} }
    void run() { new Thread(new Runnable() { public void run() { Qux.go(); } }).start(); }
}
class Base {} class Foo {} class Bar {} class Baz {} class Qux {}
"""})
    graph = scan(tmp_path)
    assert "p.A.Inner.Deep" in graph.nodes and graph.nodes["p.A.Inner"].kind == "interface"
    for target in ("Foo", "Bar", "Baz", "Base"):
        edge(graph, "p.A", f"p.{target}", "dependency" if target != "Foo" else "association")
    assert calls(edge(graph, "p.A", "p.Qux", "dependency")) == {"go": 1}  # anonymous class = run()


def test_cli_scan_graph_and_outline(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    data = tmp_path / "data"
    data.mkdir()
    main(["scan", str(FIXTURE), "-o", str(data / "graph.json")])
    capsys.readouterr()
    main(["graph", str(FIXTURE), "5", "--data", str(data)])
    graph = capsys.readouterr().out
    assert "Cart" in graph and "Pricing" in graph, graph
    main(["outline", str(FIXTURE), "Cart", "--data", str(data)])
    outline = capsys.readouterr().out
    assert "total" in outline and "src/main/java/com/shop/service/Cart.java:24-29" in outline, outline
