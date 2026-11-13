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
    # style.css has no node, so it stays an outside name, but a found repo file (never
    # unresolved); URLs and inline scripts give nothing.
    refs = graph.nodes["index.html"].external_refs.values()
    assert [(ref.to, ref.repo_file) for ref in refs] == [("style.css", True)]
    assert not [w for w in graph.nodes["index.html"].warnings if w["kind"] == "unresolved"]


def test_relative_and_root_paths_missing_files_skipped():
    graph = scan()
    edge(graph, "pages/about.html", "js/app.js", "dependency")
    assert graph.nodes["pages/about.html"].external_refs == {}


def test_stray_greater_than_in_text_is_no_parse_error(tmp_path):
    """tree-sitter-html errors on `a > b` in text, which HTML allows; a real error still counts."""
    (tmp_path / "ok.html").write_text("<pre>cli ..> views\n<span>x</span> a > b</pre>\n<p>c >d</p>\n")
    (tmp_path / "bad.html").write_text("<p>a > b</p>\n<div <p>\n")
    graph = scan_repo(tmp_path, modules=[HtmlModule()], use_cache=False)
    assert graph.diagnostics.get("parse_errors", {}) == {"bad.html": 2}


def test_pages_are_never_unused():
    """A person opens a page in a browser; code rarely points at it (DESIGN 12a)."""
    graph = scan()
    assert all({"kind": "unused"} not in graph.nodes[page].warnings
               for page in ("index.html", "pages/about.html"))
