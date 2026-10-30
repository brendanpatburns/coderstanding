from pathlib import Path

from coderstanding.languages.html import HtmlModule
from coderstanding.languages.javascript import JavaScriptModule
from coderstanding.scan import scan_repo
from helpers import edge

FIXTURES = Path(__file__).parent / "fixtures"


def scan():
    return scan_repo(FIXTURES / "html_basic", modules=[HtmlModule(), JavaScriptModule()],
                     use_cache=False)


def test_page_is_a_module_linking_repo_scripts_and_styles():
    graph = scan()
    assert graph.nodes["index.html"].kind == "module"
    assert edge(graph, "index.html", "js/app.js", "dependency").detail == ["import"]
    # style.css has no node, so it stays an outside name; URLs and inline scripts give nothing.
    assert {to for (_, to, _) in graph.nodes["index.html"].external_refs} == {"style.css"}


def test_relative_and_root_paths_missing_files_skipped():
    graph = scan()
    edge(graph, "pages/about.html", "js/app.js", "dependency")
    assert graph.nodes["pages/about.html"].external_refs == {}
