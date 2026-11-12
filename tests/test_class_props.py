"""Public properties of a class (DESIGN 12c Properties): the visibility rule per language and
the fan-out JSON. Functions are never filtered by visibility."""

import tempfile

import pytest

from coderstanding.languages.csharp import CSharpModule
from coderstanding.languages.gdscript import GDScriptModule
from coderstanding.languages.java import JavaModule
from coderstanding.languages.javascript import JavaScriptModule
from coderstanding.languages.kotlin import KotlinModule
from coderstanding.languages.python import PythonModule
from coderstanding.model import Member, is_public
from coderstanding.scan import scan_repo
from coderstanding.views import compute_view
from helpers import fan_graph, write_files

SOURCES = {
    "csharp": (CSharpModule, "A.cs", """
public class A { public int Open { get; set; } int hidden; private int secret;
  public Foo field; void _go() { field.Hit(); } }
public class Foo { public void Hit() {} }
"""),
    "java": (JavaModule, "A.java", """
public class A { public int open; int hidden; private int secret; Foo foo;
  void _go() { foo.hit(); } }
class Foo { void hit() {} }
"""),
    "kotlin": (KotlinModule, "A.kt", """
class A { val open: Int = 1; private val secret: Int = 2; internal val inner: Int = 3 }
"""),
    "gdscript": (GDScriptModule, "a.gd", """
class_name A
extends Node
var open: int = 1
var _secret: int = 2
func _go():
\tpass
"""),
    "python": (PythonModule, "a.py", """
class Foo:
    def hit(self): pass

class A:
    def __init__(self):
        self.open = Foo()
        self._secret = Foo()
    def _go(self):
        self.open.hit()
"""),
    "javascript": (JavaScriptModule, "a.js", """
class Foo { hit() {} }
class A { constructor() { this.open = new Foo(); this._secret = new Foo(); }
  _go() { this.open.hit(); } }
"""),
}
PUBLIC = {"csharp": {"Open", "field"}, "java": {"open"}, "kotlin": {"open"}, "gdscript": {"open"},
          "python": {"open"}, "javascript": {"open"}}


@pytest.mark.parametrize("lang", sorted(SOURCES))
def test_public_fields_per_language(lang):
    module, name, source = SOURCES[lang]
    with tempfile.TemporaryDirectory() as root:
        write_files(root, {name: source})
        graph = scan_repo(root, modules=[module()])
    node = next(n for n in graph.nodes.values() if n.name == "A")
    fields = [m for m in node.members if m.kind in ("field", "property", "const")]
    assert {m.name for m in fields if m.public} == PUBLIC[lang]
    assert len(fields) > len(PUBLIC[lang])  # the hidden ones are still recorded
    functions = {m.name for m in node.members if m.kind in ("method", "function")}
    if lang != "kotlin":
        assert functions & {"_go"}  # a private / `_` function is still listed


def test_is_public_rules():
    assert is_public("x", "csharp", {"public", "static"}) and not is_public("x", "csharp")
    assert not is_public("x", "java", {"private"})
    assert is_public("x", "kotlin") and not is_public("x", "kotlin", {"internal"})
    assert not is_public("_x", "python") and not is_public("_x", "gdscript")
    assert not is_public("#x", "javascript") and not is_public("x", "javascript", {"protected"})
    assert is_public("_x", "cpp")  # C++ readers do not track access sections


def test_fanout_json_lists_public_properties_and_every_function():
    graph = fan_graph()
    player = graph.nodes["Player"]
    player.members.append(Member("_hidden", "field", "VFX", public=False))
    view = compute_view(graph, None, level=1, expand=["Player"])
    parts = view["expanded"]["Player"]
    assert [p["name"] for p in parts["properties"]] == ["vfx"]
    assert {f["name"] for f in parts["fields"]} == {"vfx", "_hidden"}
    functions = {f["name"]: f for f in parts["functions"]}
    assert "_physics_process" in functions  # a `_` function stays listed
    assert any(l["from_fn"] == "_physics_process" and l["to_fn"] == "play_step" for l in view["fanout"])
    node = next(n for n in view["nodes"] if n["id"] == "Player")
    assert node["properties"] == 1 and node["fields"] == 2


def test_hidden_signal_and_typescript_modifiers():
    with tempfile.TemporaryDirectory() as root:
        write_files(root, {"a.gd": "class_name G\nextends Node\nsignal done\nsignal _quiet\n",
                           "b.ts": "class Foo {}\nclass T { private a: Foo; protected b: Foo; c: Foo; }\n"})
        graph = scan_repo(root, modules=[GDScriptModule(), JavaScriptModule()])
    public = {n.name: {m.name for m in n.members if m.public and m.kind in ("signal", "field")}
              for n in graph.nodes.values()}
    assert public["G"] == {"done"} and public["T"] == {"c"}


def test_member_json_keeps_public_only_when_hidden():
    from coderstanding.graph_json import member_to_json
    assert "public" not in member_to_json(Member("a", "field"))
    assert member_to_json(Member("a", "field", public=False))["public"] is False


def test_underscore_rpc_function_stays_a_listed_message():
    with tempfile.TemporaryDirectory() as root:
        write_files(root, {"a.gd": "class_name G\nextends Node\n@rpc(\"any_peer\")\nfunc _on_sync():\n\tpass\n"})
        graph = scan_repo(root, modules=[GDScriptModule()])
    member = next(m for n in graph.nodes.values() if n.name == "G" for m in n.members if m.name == "_on_sync")
    assert member.rpc and member.public  # visibility only filters Properties, never functions or messages


def test_outline_marks_fields_that_are_not_public():
    from coderstanding.notes import Notes
    from coderstanding.views import outline
    graph = fan_graph()
    graph.nodes["Player"].members.append(Member("_hidden", "field", "VFX", public=False))
    fields = {f["name"]: f["public"] for f in outline(graph, Notes(), "Player")["fields"]}
    assert fields["_hidden"] is False and fields["vfx"] is True
