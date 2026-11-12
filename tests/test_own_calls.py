"""Calls between functions of one module or class (own calls, `Ref.own_call`): a self edge per
node that impact/ripple walk, never a box-to-box line or a hub/god count; plus JS module functions
inside a top-level IIFE as module members."""

from coderstanding.checks import neighbours
from coderstanding.languages.cpp import CppModule
from coderstanding.languages.javascript import JavaScriptModule
from coderstanding.languages.python import PythonModule
from coderstanding.languages.shell import ShellModule
from coderstanding.scan import scan_repo
from coderstanding.views import compute_view, ripple
from helpers import edge, write_files


def scan(tmp_path, files, module):
    write_files(tmp_path, files)
    return scan_repo(tmp_path, modules=[module])


def own_pairs(graph, node_id):
    """{(calling function, called function)} of the node's self edge."""
    return {(c.from_member, c.member) for c in edge(graph, node_id, node_id, "dependency").calls}


PYTHON = {
    "cli.py": ("def plan_text(count):\n    return str(count)\n\n\n"
               "def run_graph(args):\n    return plan_text(1)\n\n\n"
               "def main(argv):\n    return run_graph(argv)\n\n\n"
               "class Box:\n    def draw(self):\n        return self.size() + plan_text(2)\n\n"
               "    def size(self):\n        return 1\n\n"
               "    @classmethod\n    def make(cls):\n        return cls.size(None)\n"),
}


def test_python_module_functions_and_methods_call_their_own(tmp_path):
    graph = scan(tmp_path, PYTHON, PythonModule())
    assert own_pairs(graph, "cli") == {("run_graph", "plan_text"), ("main", "run_graph")}
    assert own_pairs(graph, "cli.Box") == {("draw", "size"), ("make", "size")}
    assert ("cli.Box", "cli", "dependency") not in graph.edges  # a class -> its module is no own call


def test_ripple_walks_own_calls_up_the_module(tmp_path):
    graph = scan(tmp_path, PYTHON, PythonModule())
    assert ripple(graph, {("cli", "plan_text")}, 3) == [[("cli", "run_graph")], [("cli", "main")]]


def test_own_calls_draw_no_line_and_add_no_neighbour(tmp_path):
    graph = scan(tmp_path, PYTHON, PythonModule())
    view = compute_view(graph, level=5)
    assert all(e["from"] != e["to"] for e in view["edges"])
    assert neighbours(graph, set(graph.nodes))["cli"] == set()


JS_IIFE = {
    "viewer/help.js": ("// Tips.\n(function () {\nlet rules = {};\n"
                       "function kindWords(kind) { return rules[kind]; }\n"
                       "const legend = (kinds) => kinds.map((k) => kindWords(k));\n"
                       "function later() {}\n"
                       "class Tip { show() { return this.text(); } text() { return ''; } }\n"
                       "window.Help = { legend };\n})();\n"),
    "viewer/arrow.js": "(() => {\nfunction one() { two(); }\nfunction two() {}\n})();\n",
}


def test_js_iife_functions_are_module_members(tmp_path):
    graph = scan(tmp_path, JS_IIFE, JavaScriptModule())
    members = {m.name: m for m in graph.nodes["viewer/help.js"].members}
    assert set(members) == {"kindWords", "legend", "later"}
    assert members["later"].stub
    assert "viewer/help.js.Tip" in graph.nodes
    assert {m.name for m in graph.nodes["viewer/arrow.js"].members} == {"one", "two"}


def test_js_own_calls_of_module_and_class(tmp_path):
    graph = scan(tmp_path, JS_IIFE, JavaScriptModule())
    assert own_pairs(graph, "viewer/help.js") == {("legend", "kindWords")}
    assert own_pairs(graph, "viewer/help.js.Tip") == {("show", "text")}
    assert own_pairs(graph, "viewer/arrow.js") == {("one", "two")}


def test_js_imported_name_is_not_an_own_call(tmp_path):
    files = {"a.js": "export function go() {}\n",
             "b.js": "import { go } from './a';\nexport function run() { go(); }\n"}
    graph = scan(tmp_path, files, JavaScriptModule())
    assert ("b.js", "b.js", "dependency") not in graph.edges
    assert ("b.js", "a.js", "dependency") in graph.edges


def test_c_and_cpp_own_calls(tmp_path):
    files = {"tool.c": "static int helper(void) { return 1; }\nint main(void) { return helper(); }\n",
             "box.cpp": ("class Box {\npublic:\n  int size() { return 1; }\n"
                         "  int area() { return size() * size(); }\n};\n")}
    graph = scan(tmp_path, files, CppModule())
    assert own_pairs(graph, "tool") == {("main", "helper")}
    assert own_pairs(graph, "box.Box") == {("area", "size")}


def test_shell_own_calls_still_recorded(tmp_path):
    graph = scan(tmp_path, {"run.sh": "build() { :; }\nmain() { build; }\nmain\n"}, ShellModule())
    assert own_pairs(graph, "run.sh") == {("main", "build"), ("<init>", "main")}
