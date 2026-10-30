import subprocess

from coderstanding.filewalk import assign_files, list_files
from fake_lang import FAKE
from helpers import write_files

FILES = {
    "src/a.fk": "", "src/a.txt": "", "x.fk": "",
    "addons/p/b.fk": "", ".godot/c.fk": "", "bin/d.fk": "", "obj/e.fk": "",
    "node_modules/m/f.fk": "", ".venv/g.fk": "", "deep/obj/h.fk": "",
}


def test_skips_build_and_vendor_dirs(tmp_path):
    write_files(tmp_path, FILES)
    assert list_files(tmp_path, ["*.fk"]) == ["src/a.fk", "x.fk"]


def test_skip_dirs_are_configurable(tmp_path):
    write_files(tmp_path, FILES)
    files = list_files(tmp_path, ["*.fk"], skip_dirs=[".godot", "bin", "obj", "node_modules",
                                                      ".venv"])
    assert "addons/p/b.fk" in files


def test_path_patterns_and_git_ignore(tmp_path):
    write_files(tmp_path, {**FILES, ".gitignore": "ignored/\n", "ignored/i.fk": "",
                           "project.godot": "", "sub/project.godot": ""})
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert list_files(tmp_path, ["*.fk"]) == ["src/a.fk", "x.fk"]
    assert list_files(tmp_path, ["src/*.txt", "project.godot"]) == [
        "project.godot", "src/a.txt", "sub/project.godot"]


def test_assign_files_pairs_files_with_modules(tmp_path):
    write_files(tmp_path, FILES)
    assert assign_files(tmp_path, [FAKE]) == [("src/a.fk", FAKE), ("x.fk", FAKE)]
