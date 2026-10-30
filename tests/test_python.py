from pathlib import Path

from coderstanding.languages.python import PythonModule
from coderstanding.scan import scan_repo
from helpers import edge

FIXTURE = Path(__file__).parent / "fixtures" / "python_basic"


def edges(graph, kind):
    return {(f, t) for (f, t, k) in graph.edges if k == kind}


def scan():
    return scan_repo(FIXTURE, modules=[PythonModule()])



def test_module_ids_and_class_children():
    graph = scan()
    assert {"main", "shop", "shop.util", "shop.models", "shop.cart"} <= set(graph.nodes)
    assert graph.nodes["shop.cart"].kind == "module"
    assert graph.nodes["shop.cart.Cart"].kind == "class"
    assert graph.nodes["main"].flags == ["script-main", "entry"]


def test_protocol_becomes_interface_and_realization():
    graph = scan()
    assert graph.nodes["shop.models.Priced"].kind == "interface"
    assert ("shop.cart.Cart", "shop.models.Priced") in edges(graph, "realization")


def test_generalization():
    assert ("shop.models.Gift", "shop.models.Item") in edges(scan(), "generalization")


def test_composition_association_and_field_calls():
    graph = scan()
    assert ("shop.cart.Cart", "shop.models.Item") in edges(graph, "composition")
    assert ("shop.cart.Cart", "shop.models.Receipt") in edges(graph, "association")
    held = edge(graph, "shop.cart.Cart", "shop.cart.Ledger", "composition")
    assert [c.member for c in held.calls] == ["record"]
    assert ("shop.models.Receipt", "shop.models.Item") in edges(graph, "association")


def test_class_level_annotation_folds_into_composition():
    graph = scan()
    assert ("shop.cart.Cart", "shop.cart.Ledger") not in edges(graph, "association")


def test_imports_relative_and_absolute():
    graph = scan()
    deps = edges(graph, "dependency")
    assert ("shop.models", "shop.util") in deps
    assert ("shop.cart", "shop.models.Item") in deps
    assert ("main", "shop.cart") in deps


def test_calls_to_imported_names_and_modules():
    graph = scan()
    assert ("shop.cart.Cart", "shop.util") in edges(graph, "dependency")
    assert ("shop.models.Item", "shop.util") in edges(graph, "dependency")
    call = edge(graph, "shop.models.Item", "shop.util", "dependency")
    assert [c.member for c in call.calls] == ["format_price"]
    assert ("main", "shop.cart.Cart") in edges(graph, "dependency")


def test_type_hints_are_dependencies():
    graph = scan()
    assert "param" in edge(graph, "shop.cart.Cart", "shop.models.Item", "dependency").detail
    assert ("shop.cart.Cart", "shop.cart.Cart") in edges(graph, "dependency")  # internal call


def test_outside_names_become_external_refs():
    node = scan().nodes["shop.cart"]
    assert ("shop.cart", "os", "dependency") in node.external_refs
    cart = scan().nodes["shop.cart.Cart"]
    assert ("shop.cart.Cart", "os", "dependency") in cart.external_refs
    assert cart.external_refs[("shop.cart.Cart", "os", "dependency")].calls[0].member == "getcwd"


def test_from_member(tmp_path):
    (tmp_path / "util.py").write_text("def go(): pass\nclass Foo: pass\n")
    (tmp_path / "m.py").write_text("""from util import go, Foo
go()
class A(Foo):
    held: Foo
    def run(self, p: Foo):
        go()
        f = lambda: go()
    async def later(self):
        go()
""")
    from coderstanding.language import FileContext
    result = PythonModule().parse(FileContext(tmp_path, "m.py"))
    found = [(r.detail, r.call, r.from_member) for r in result.refs
             if r.detail not in ("import",) and r.kind != "generalization"]
    assert ("call", "go", None) not in found
    assert ("call", "go", "<init>") in found
    assert ("field", "", "<field>") in found
    assert ("param", "", "run") in found
    assert found.count(("call", "go", "run")) == 2  # incl. the lambda
    assert ("call", "go", "later") in found
    assert [r.from_member for r in result.refs if r.kind == "generalization"] == [None]


def test_class_size_is_its_span(tmp_path):
    (tmp_path / "m.py").write_text("import os\n\n\nclass A:\n    def f(self):\n        pass\n\n\nX = 1\n")
    graph = scan_repo(tmp_path, modules=[PythonModule()], use_cache=False)
    assert (graph.nodes["m.A"].lines, graph.nodes["m.A"].span) == (3, True)
    assert (graph.nodes["m"].lines, graph.nodes["m"].span) == (9, False)
