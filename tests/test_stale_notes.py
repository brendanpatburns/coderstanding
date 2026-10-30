from coderstanding.checks import warnings_by_kind
from coderstanding.model import Graph
from coderstanding.notes import Notes, load_notes, save_notes
from coderstanding.scan import scan_repo
from coderstanding.textout import format_warnings
from coderstanding.views import compute_view
from helpers import write_files

CODE = """class Player:
    def hit(self):
        self.hp -= 1

    def heal(self):
        self.hp += 1


class World:
    def run(self):
        p = Player()
        p.hit()
"""


def scan(root):
    return scan_repo(root, use_cache=False)


def fp_graph(**fingerprints):
    graph = Graph()
    graph.fingerprints = dict(fingerprints)
    return graph


def test_fingerprints_cover_class_function_and_edge(tmp_path):
    write_files(tmp_path, {"m.py": CODE})
    fps = scan(tmp_path).fingerprints
    assert {"node:m.Player", "node:m.Player#hit", "node:m.Player#heal"} <= set(fps)
    assert any(k.startswith("edge:m.World") for k in fps)


def test_whitespace_and_moves_do_not_change_fingerprints(tmp_path):
    write_files(tmp_path, {"m.py": CODE})
    before = scan(tmp_path).fingerprints
    write_files(tmp_path, {"m.py": "# new header\n\n" + CODE.replace("self.hp -= 1", "self.hp  -=  1")})
    after = scan(tmp_path).fingerprints  # same code, shifted lines (the module node holds the header)
    assert {k: v for k, v in after.items() if k != "node:m"} == {k: v for k, v in before.items() if k != "node:m"}


def test_note_is_stale_after_code_edit_only_for_what_changed(tmp_path):
    write_files(tmp_path, {"m.py": CODE})
    graph, notes = scan(tmp_path), Notes()
    notes.set("node:m.Player#hit", "you", {"hint": "loses 1 hp"}, graph=graph)
    notes.set("node:m.Player#heal", "you", {"hint": "gains 1 hp"}, graph=graph)
    assert notes.stale_notes(graph) == []
    write_files(tmp_path, {"m.py": CODE.replace("self.hp -= 1", "self.hp -= 5")})
    graph = scan(tmp_path)
    assert notes.stale_notes(graph) == [("node:m.Player#hit", ["hint"])]


def test_resave_and_still_true_refresh(tmp_path):
    write_files(tmp_path, {"m.py": CODE})
    notes = Notes()
    notes.set("node:m.Player", "you", {"hint": "a", "review": "b"}, graph=scan(tmp_path))
    write_files(tmp_path, {"m.py": CODE.replace("+= 1", "+= 2")})
    graph = scan(tmp_path)
    assert notes.stale_fields("node:m.Player", graph) == ["hint", "review"]
    notes.set("node:m.Player", "you", {"hint": "a"}, graph=graph)  # re-save one field
    assert notes.stale_fields("node:m.Player", graph) == ["review"]
    assert notes.confirm("node:m.Player", graph) == ["hint", "review"]
    assert notes.stale_fields("node:m.Player", graph) == []


def test_old_notes_without_fingerprint_are_unknown_not_stale():
    notes = Notes()
    notes.set("node:A", "you", {"hint": "old"})  # no graph: no fingerprint
    assert notes.stale_fields("node:A", fp_graph(**{"node:A": "abc"})) == []


def test_fingerprints_survive_save_and_load(tmp_path):
    notes = Notes()
    notes.set("node:A", "you", {"hint": "x"}, graph=fp_graph(**{"node:A": "abc"}))
    save_notes(notes, tmp_path / "n.jsonl")
    again = load_notes(tmp_path / "n.jsonl")
    assert again.stale_fields("node:A", fp_graph(**{"node:A": "abc"})) == []
    assert again.stale_fields("node:A", fp_graph(**{"node:A": "zzz"})) == ["hint"]


def test_clearing_a_field_drops_its_fingerprint():
    notes = Notes()
    notes.set("node:A", "you", {"hint": "x"}, graph=fp_graph(**{"node:A": "abc"}))
    notes.set("node:A", "you", {"hint": None})
    assert notes.entries == {}


def test_view_text_and_warnings_show_stale(tmp_path):
    write_files(tmp_path, {"m.py": CODE})
    notes = Notes()
    notes.set("node:m.Player", "you", {"hint": "the hero"}, graph=scan(tmp_path))
    write_files(tmp_path, {"m.py": CODE.replace("+= 1", "+= 2")})
    graph = scan(tmp_path)
    view = compute_view(graph, notes, level=5)
    player = next(n for n in view["nodes"] if n["id"] == "m.Player")
    assert player["stale"] == ["hint"]
    groups = warnings_by_kind(graph, notes)
    assert "stale (code changed since the note): m.Player hint" in format_warnings("app", groups)
    from coderstanding.textout import format_node
    text = format_node("app", view, "m.Player", short_name=str)
    assert "the hero (stale)" in text
