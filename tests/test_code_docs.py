"""Descriptions from the code's own header comments, per language, and their precedence."""

from coderstanding.languages.cpp import CppModule
from coderstanding.languages.csharp import CSharpModule
from coderstanding.languages.doc_text import DOC_MAX, trim_doc
from coderstanding.languages.gdscript import GDScriptModule
from coderstanding.languages.javascript import JavaScriptModule
from coderstanding.languages.python import PythonModule
from coderstanding.notes import Notes, node_key
from coderstanding.scan import scan_repo
from coderstanding.views import compute_view
from helpers import write_files


def docs(tmp_path, files, *modules):
    write_files(tmp_path, files)
    graph = scan_repo(tmp_path, modules=list(modules))
    return graph, {i: n.doc for i, n in graph.nodes.items() if n.doc}


def test_trim_keeps_first_paragraph_on_one_line_and_caps_length():
    assert trim_doc(["Does a thing", "over two lines.", "", "Second paragraph."]) == \
        "Does a thing over two lines."
    assert trim_doc(["Short.", "@param x the x"]) == "Short."
    assert len(trim_doc(["word " * 400])) == DOC_MAX


def test_gdscript_file_doc_inner_class_doc_and_header_skips(tmp_path):
    _, found = docs(tmp_path, {
        "a.gd": "# Copyright 2026 Someone\n\n## The player.\n## Moves around.\n\n@tool\n"
                "extends Node\nclass_name Player\n\n## Inner helper.\nclass Helper:\n\tpass\n\n"
                "## about speed\nvar speed = 1\n",
        "b.gd": "#!/usr/bin/env -S godot -s\n# SPDX-License-Identifier: MIT\n\n"
                "# Plain intro to b.\nextends Node\n",
        "c.gd": "extends Node\n## About var only\nvar x = 1\n",
    }, GDScriptModule())
    assert found == {"Player": "The player. Moves around.", "Player.Helper": "Inner helper.",
                     "res://b.gd": "Plain intro to b."}


def test_python_docstrings_and_comment_fallback(tmp_path):
    _, found = docs(tmp_path, {
        "m.py": '"""Module doc.\n\nMore."""\n\n\nclass K:\n    """Class doc."""\n    x = 1\n',
        "n.py": "#!/usr/bin/env python3\n# Plain note about n.\n\nx = 1\n",
        "o.py": "# Licensed under the MIT license\nx = 1\n",
    }, PythonModule())
    assert found == {"m": "Module doc.", "m.K": "Class doc.", "n": "Plain note about n."}


def test_csharp_summary(tmp_path):
    _, found = docs(tmp_path, {
        "A.cs": "/// <summary>\n/// Handles <c>things</c>.\n/// </summary>\n[Tool]\nclass A { }\n"
                "// not a doc\nclass B { }\n"}, CSharpModule())
    assert found == {"A": "Handles things."}


def test_js_jsdoc_and_leading_comment(tmp_path):
    _, found = docs(tmp_path, {
        "a.js": "// Helpers for carts.\n\nimport x from './x.js'\n\n"
                "/**\n * A cart.\n * @param n count\n */\nexport class Cart {}\n",
        "x.js": "export const x = 1\n"}, JavaScriptModule())
    assert found == {"a.js": "Helpers for carts.", "a.js.Cart": "A cart."}


def test_cpp_block_comments(tmp_path):
    _, found = docs(tmp_path, {
        "w.h": "/** Widget header. */\n#pragma once\n\n/** A widget. */\nclass Widget { };\n"},
        CppModule())
    assert found["w"] == "Widget header." and found["Widget"] == "A widget."


def test_description_precedence_human_over_ai_over_code(tmp_path):
    graph, _ = docs(tmp_path, {"m.py": '"""Code says."""\nx = 1\n'}, PythonModule())

    def shown(notes):
        node = compute_view(graph, notes, level=5)["nodes"][0]
        return node["description"], node["description_by"]

    assert shown(Notes()) == ("Code says.", "code")
    notes = Notes()
    notes.set(node_key("m"), "claude", {"description": "AI says."})
    assert shown(notes) == ("AI says.", "claude")
    notes.set(node_key("m"), "you", {"description": "I say."})
    assert shown(notes) == ("I say.", "you")
