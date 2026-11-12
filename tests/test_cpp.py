from pathlib import Path

import pytest

from coderstanding.language import FileContext
from coderstanding.languages.cpp import CppModule, parse_tree
from coderstanding.scan import scan_repo
from helpers import calls, edge, write_files

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def graph():
    return scan_repo(FIXTURES / "cpp_basic", modules=[CppModule()], use_cache=False)


def externals(graph, node_id):
    return {to for (_, to, _) in graph.nodes[node_id].external_refs}


def test_header_and_source_pair_is_one_module(graph):
    util = graph.nodes["src/geo/util"]
    assert util.kind == "module"
    assert [m.name for m in util.members] == ["clamp", "log_msg", "handle_new"]  # header only
    assert "src/geo/util.h" not in graph.nodes and "src/geo/util.cc" not in graph.nodes
    assert "entry" in graph.nodes["src/app/main"].flags


def test_types_with_namespaces_kinds_and_flags(graph):
    nodes = graph.nodes
    assert nodes["geo.IShape"].kind == "interface"
    assert "abstract" in nodes["geo.Polygon"].flags
    assert nodes["geo.Square"].kind == "class"
    assert nodes["geo.Square.Corner"].kind == "inner"
    assert nodes["app.Palette"].kind == "struct"
    # Types declared in a source file are file-local, under the module.
    assert nodes["src/app/canvas.app.Brush"].kind == "struct"


def test_bases_realization_and_generalization(graph):
    edge(graph, "geo.Polygon", "geo.IShape", "realization")
    edge(graph, "geo.Square", "geo.Polygon", "generalization")


def test_fields_by_value_unique_ptr_pointer_reference_shared_ptr(graph):
    edge(graph, "app.Canvas", "app.Renderer", "composition")  # unique_ptr<Renderer>
    edge(graph, "app.Canvas", "geo.Square", "composition")  # by value
    edge(graph, "app.Canvas", "app.Palette", "association")  # Palette *
    edge(graph, "app.Canvas", "app.Theme", "association")  # shared_ptr<Theme>, Theme &
    edge(graph, "geo.Square", "geo.Grid", "composition")  # template args stripped
    assert ("app.Canvas", "std.unique_ptr", "composition") not in graph.edges


def test_out_of_line_method_calls_attach_to_the_class(graph):
    # Square::area() in shapes.cc calls util::clamp declared in util.h, and corner_->x.
    clamp = edge(graph, "geo.Square", "src/geo/util", "dependency")
    assert calls(clamp) == {"clamp": 1}
    draw = edge(graph, "app.Canvas", "src/geo/shapes", "dependency")
    assert calls(draw) == {"area_of": 1}
    edge(graph, "app.Canvas", "app.Renderer", "composition")


def test_free_function_calls_resolve_to_the_declaring_header_module(graph):
    assert calls(edge(graph, "app.Canvas", "src/geo/util", "dependency")) == {"log_msg": 1}
    assert calls(edge(graph, "src/legacy/mesh", "src/geo/util", "dependency")) == {
        "log_msg": 1}
    assert "printf" in externals(graph, "src/geo/util")
    # Local static helpers and macros make no refs.
    assert "helper" not in externals(graph, "src/geo/util")
    assert "CHECK" not in externals(graph, "app.Canvas")


def test_new_locals_and_params_are_dependencies(graph):
    brush = edge(graph, "app.Canvas", "src/app/canvas.app.Brush", "dependency")
    assert set(brush.detail) == {"local", "create"}
    assert "param" in edge(graph, "app.Canvas", "geo.IShape", "dependency").detail
    assert "call" in edge(graph, "src/geo/shapes", "geo.IShape", "dependency").detail


def test_includes(graph):
    edge(graph, "src/app/canvas", "src/geo/util", "dependency")  # "../geo/util.h"
    edge(graph, "src/app/canvas", "src/geo/shapes", "dependency")  # "geo/shapes.h", include path
    assert {"vector", "memory"} <= externals(graph, "src/app/canvas")


def test_c_file_structs_and_c_header_fallback(graph):
    mesh = graph.nodes["src/legacy/mesh.Mesh"]  # typedef struct Mesh {...} Mesh;
    assert mesh.kind == "struct" and mesh.lang == "cpp"
    edge(graph, "src/legacy/mesh.Mesh", "src/legacy/mesh.Vert", "association")
    edge(graph, "src/legacy/mesh.Mesh", "Edge", "composition")
    # edge.h uses C++ keywords as field names: read with the C grammar.
    assert {m.name for m in graph.nodes["Edge"].members} == {"class", "new"}


def test_using_and_typedef_aliases_resolve_to_the_named_type(graph):
    # `using detail::VecBase;` and `using Vec2 = VecBase<double, 2>;` in namespace geo.
    edge(graph, "app.Layer", "geo.detail.VecBase", "composition")
    edge(graph, "src/geo/handle.OpaqueHandle", "geo.detail.VecBase", "composition")


def test_opaque_type_defined_in_a_source_file_is_found_from_other_files(graph):
    # util.h: `typedef struct OpaqueHandle OpaqueHandle;`, body in handle.cc.
    edge(graph, "app.Layer", "src/geo/handle.OpaqueHandle", "association")
    edge(graph, "src/geo/util", "src/geo/handle.OpaqueHandle", "dependency")


def test_anonymous_namespace_types_stay_file_local(tmp_path):
    write_files(tmp_path, {"a.cc": "namespace { struct Scratch { int n; }; }\n",
                           "b.cc": "struct User { Scratch s; };\n"})
    result = scan_repo(tmp_path, modules=[CppModule()], use_cache=False)
    assert "a.Scratch" in result.nodes
    assert "Scratch" in externals(result, "b.User")


def test_macros_defined_in_the_file_make_no_calls(graph):
    assert not {"local_log", "SQUARE"} & externals(graph, "src/geo/handle")
    assert calls(edge(graph, "src/geo/handle", "src/geo/util", "dependency")) == {}


def test_header_grammar_choice():
    assert not parse_tree("a.h", b"struct E { int class; };").root_node.has_error
    assert not parse_tree("a.hh", b"class A { public: int x; };").root_node.has_error


def test_deep_expression_does_not_hit_recursion_limit(tmp_path):
    body = " + ".join(["x"] * 3000)
    write_files(tmp_path, {"deep.c": f"int f(int x) {{ return {body}; }}\n"})
    result = scan_repo(tmp_path, modules=[CppModule()], use_cache=False)
    assert "deep" in result.nodes


def test_cached_file_rereads_when_a_missing_include_appears(tmp_path):
    root, cache = tmp_path / "repo", tmp_path / "cache"
    write_files(root, {"dir/a.cc": '#include "x.h"\n', "other/x.h": "void x();\n"})
    first = scan_repo(root, modules=[CppModule()], cache_dir=cache)
    edge(first, "dir/a", "other/x", "dependency")  # found by name (include path)
    write_files(root, {"dir/x.h": "void y();\n"})
    second = scan_repo(root, modules=[CppModule()], cache_dir=cache)
    edge(second, "dir/a", "dir/x", "dependency")  # the header beside it wins


def test_three_part_nested_namespace(tmp_path):
    write_files(tmp_path, {"a.hh": "namespace x::y::z { struct S {}; }\n"})
    result = scan_repo(tmp_path, modules=[CppModule()], use_cache=False)
    assert "x.y.z.S" in result.nodes


def test_from_member_per_enclosing_function(tmp_path):
    source = """#include <vector>
struct Base {};
struct Item {};
struct Tool {};
struct Box : Base {
    Item held;
    void run();
    Box();
};
Box::Box() { Tool t; }
void Box::run() { Tool t; helper(); }
void helper() { Item i; }
"""
    write_files(tmp_path, {"a.cc": source})
    refs = CppModule().parse(FileContext(tmp_path, "a.cc")).refs
    found = {(r.to.name, r.detail, r.call): r.from_member for r in refs}
    assert found[("Base", "", "")] is None
    assert found[("vector", "import", "")] is None
    assert found[("Item", "field", "")] == "<field>"
    assert {r.from_member for r in refs if r.to.name == "Tool"} == {"Box", "run"}
    assert {r.from_member for r in refs if r.to.name == "Item" and r.detail != "field"} == {"helper"}


TYPEDEF_FILES = {
    "types.h": "#include <stdint.h>\ntypedef uint32_t u32;\ntypedef unsigned char u8;\ntypedef u32 addr;\n"
               "typedef struct Ship { int hp; } Ship;\ntypedef struct Ship ShipAlias;\n",
    "game.c": '#include "types.h"\nstatic u32 count;\nu8 bytes[4];\n'
              'void run(addr where, Ship *ship, Missing *gone) { u32 local = 0; }\n',
}


def test_typedefs_of_basic_types_are_known_but_struct_typedefs_stay_nodes(tmp_path):
    write_files(tmp_path, TYPEDEF_FILES)
    graph = scan_repo(tmp_path, modules=[CppModule()], use_cache=False)
    assert externals(graph, "game") == {"Missing"}  # u32, u8, addr give no external reference
    assert "types.Ship" in graph.nodes or "Ship" in graph.nodes
    assert not any(node.name in ("u32", "u8", "addr") for node in graph.nodes.values())


# ---- decompiler output and function-defining macros ---------------------------------------

def scan_c(tmp_path, files, **settings):
    write_files(tmp_path, files)
    return scan_repo(tmp_path, modules=[CppModule()], use_cache=False, **settings)


GHIDRA_FILES = {
    "a.c": "ushort __watcall F_1(void)\n{\n  uint value;\n  value = CONCAT11(1, 2);\n  F_2();\n  return in(3);\n}\n",
    "b.c": "void __cdecl16near F_2(undefined2 param, byte *flag, code *callback)\n{\n}\n",
}


def test_compiler_words_are_blanked_and_decompiler_names_are_built_in(tmp_path):
    graph = scan_c(tmp_path, GHIDRA_FILES)
    assert [m.name for m in graph.nodes["a"].members] == ["F_1"]
    assert [m.name for m in graph.nodes["b"].members] == ["F_2"]
    assert not graph.diagnostics.get("parse-error")
    edge(graph, "a", "b", "dependency")
    unresolved = {name for node in graph.nodes.values() for (_, name, _) in node.external_refs}
    assert not unresolved & {"CONCAT11", "in", "uint", "undefined2", "byte", "code"}


def test_blank_compiler_words_keeps_length():
    from coderstanding.languages.cpp import blank_compiler_words
    source = b"void __watcall f(void); int __far x; int my__far;"
    blanked = blank_compiler_words(source)
    assert len(blanked) == len(source)
    assert b"__watcall" not in blanked and b"my__far" in blanked


REWRITE_FILES = {
    "game/x.c": "REWRITE_CHECKED(272b4, .scratch = 1,\n    .vary = 2)\n{\n  helper();\n}\n\n"
                "REWRITE(5)\n{\n}\n\nint plain(void) { return 0; }\n",
    "game/y.c": "void helper(void) {}\n",
}


def test_function_macros_define_functions(tmp_path):
    graph = scan_c(tmp_path, REWRITE_FILES, function_macros={"REWRITE_CHECKED": "F_{1}", "REWRITE": "F_{1}"})
    members = {m.name: m for m in graph.nodes["game/x"].members}
    assert set(members) == {"F_272b4", "F_5", "plain"}
    assert members["F_272b4"].line == 1 and members["plain"].line == 11  # line numbers kept
    edge(graph, "game/x", "game/y", "dependency")  # the macro body's call is read


def test_function_macros_off_by_default_and_setting_changes_cache(tmp_path):
    write_files(tmp_path, REWRITE_FILES)
    cache_dir = tmp_path / "cache"
    plain = scan_repo(tmp_path, modules=[CppModule()], cache_dir=cache_dir)
    assert "F_5" not in {m.name for m in plain.nodes["game/x"].members}
    macros = scan_repo(tmp_path, modules=[CppModule()], cache_dir=cache_dir, function_macros={"REWRITE": "F_{1}"})
    assert "F_5" in {m.name for m in macros.nodes["game/x"].members}


def test_parse_function_macros_setting():
    from coderstanding.apps import AppError, parse_setting
    assert parse_setting("function_macros", '{"REWRITE": "F_{1}"}') == {"REWRITE": "F_{1}"}
    with pytest.raises(AppError):
        parse_setting("function_macros", "[1]")
