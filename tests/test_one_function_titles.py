"""A file-level unit with exactly one function and no classes is titled by that function
(`title` in the view rows); nicknames win; text output and `find` use it."""

from coderstanding.model import Graph, Member, Node
from coderstanding.notes import Notes, node_key
from coderstanding.textout import view_text
from coderstanding.views import compute_view, find


def module(name, functions, span=False):
    return Node(id=name, name=name, kind="module", lang="c", file=f"{name}.c", level=1, span=span,
                members=[Member(function, "function", line=2 + i) for i, function in enumerate(functions)])


def make_graph():
    graph = Graph(repo="t")
    for node in (module("243e0", ["update_world"]), module("two", ["a_one", "a_two"]),
                 module("holder", ["only"]), module("empty", [])):
        graph.add_node(node)
    graph.add_node(Node(id="holder.Thing", name="Thing", kind="class", lang="c", file="holder.c", level=1))
    return graph


def titles(notes=None):
    view = compute_view(make_graph(), notes or Notes())
    return {n["id"]: n["title"] for n in view["nodes"]}


def test_a_one_function_file_is_titled_by_its_function():
    found = titles()
    assert found["243e0"] == "update_world"
    assert found["two"] is None  # two functions
    assert found["holder"] is None  # holds a class
    assert found["empty"] is None


def test_a_nickname_wins_over_the_function_name():
    notes = Notes()
    notes.set(node_key("243e0"), "Ann", {"nickname": "world_tick"})
    assert titles(notes)["243e0"] == "world_tick"


def test_text_output_shows_title_and_file_name():
    text = view_text("t", make_graph(), Notes())
    assert "update_world (243e0)" in text


def test_find_finds_the_file_by_its_function_name():
    hits = find(make_graph(), Notes(), ["update_world"])
    assert ("243e0", None) in [hit["unit"] for hit in hits]
