"""Links the scanner used to miss: browser scripts sharing globals, imports inside functions."""

from coderstanding.languages.html import HtmlModule
from coderstanding.languages.javascript import JavaScriptModule
from coderstanding.languages.python import PythonModule
from coderstanding.scan import scan_repo
from helpers import edge, write_files as write


def scan(root):
    return scan_repo(root, modules=[HtmlModule(), JavaScriptModule(), PythonModule()],
                     use_cache=False)


PAGE = '<script src="lib.js"></script>\n<script src="use.js"></script>\n'
LIB = "var state = {};\nfunction helper() {}\nclass Box { open() {} }\nwindow.api = {};\n"
USE = "function run() { helper(); const b = new Box(); state.x = 1; api.go(); }\n"


def test_classic_scripts_on_one_page_share_top_level_names(tmp_path):
    write(tmp_path, {"index.html": PAGE, "lib.js": LIB, "use.js": USE})
    graph = scan(tmp_path)
    found = edge(graph, "use.js", "lib.js", "dependency")
    assert {c.member for c in found.calls} >= {"helper", "go"}
    assert edge(graph, "use.js", "lib.js.Box", "dependency")
    assert graph.nodes["use.js"].external_refs == {}  # guesses that match nothing vanish


def test_module_scripts_and_scripts_on_other_pages_do_not_share(tmp_path):
    page = '<script src="lib.js"></script>\n<script type="module" src="use.js"></script>\n'
    write(tmp_path, {"index.html": page, "other.html": '<script src="use.js"></script>',
                     "lib.js": LIB, "use.js": USE})
    graph = scan(tmp_path)
    assert not [e for e in graph.edges.values() if e.from_id == "use.js" and e.to == "lib.js"]


def test_local_declarations_shadow_page_globals(tmp_path):
    write(tmp_path, {"index.html": PAGE, "lib.js": LIB,
                     "use.js": "function run(helper) { helper(); }\n"})
    graph = scan(tmp_path)
    assert not [e for e in graph.edges.values() if e.from_id == "use.js" and e.to == "lib.js"]


def test_python_imports_inside_functions_and_blocks(tmp_path):
    write(tmp_path, {
        "pkg/__init__.py": "", "pkg/server.py": "class Api:\n    pass\n",
        "pkg/util.py": "def tool():\n    pass\n",
        "pkg/cli.py": ("def main():\n    from .server import Api\n    Api()\n"
                       "try:\n    from . import util\nexcept ImportError:\n    util = None\n")})
    graph = scan(tmp_path)
    edge(graph, "pkg.cli", "pkg.server.Api", "dependency")
    edge(graph, "pkg.cli", "pkg.util", "dependency")


def test_js_require_and_dynamic_import_inside_functions(tmp_path):
    write(tmp_path, {"a.js": "function f() { const b = require('./b'); b.go(); }\n"
                             "async function g() { return import('./c.js'); }\n",
                     "b.js": "exports.go = 1;\n", "c.js": "exports.x = 1;\n"})
    graph = scan(tmp_path)
    edge(graph, "a.js", "b.js", "dependency")
    edge(graph, "a.js", "c.js", "dependency")
