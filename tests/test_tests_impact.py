"""tests --diff and impact finding more: tour steps, viewer e2e checks, tests reaching code through
a CLI command, helpers reached through a language module."""

import pytest

from coderstanding.cli import main
from helpers import write_files
from test_tests_diff import BODY, CORE_FILES, EDITED, git, make_repo, run

TOUR = """---
id: walk
title: A walk
by: you
steps:
  - key: k1
    targets: [Core#run]
    code: {at: Core#run, lines: +0..+2}
---
## Run
[[Core#run]] runs.
"""

VIEWER_FILES = {
    "viewer/app.js": "function drawBox(box) {\n  return box.size;\n}\n\nfunction other() {\n  return 1;\n}\n",
    "viewer/vendor/lib.js": "function vendored() {}\n",
    "tests/e2e/run.mjs": ('await step("boxes: draw, resize", async () => {\n  drawBox(1);\n});\n'
                          'await step("others", async () => {\n  other2();\n});\n'),
}

COMMAND_FILES = {
    "texts.py": ("def plan_text(count):\n    return f' plan: {count}'\n\n\n"
                 "def header(count):\n    return 'graph' + plan_text(count)\n"),
    # main dispatches by name (like argparse `set_defaults(run=...)`): no call reaches run_graph
    "cli.py": ("from texts import header\n\n\ndef run_graph(args):\n    print(header(1))\n\n\n"
               "COMMANDS = {'graph': run_graph}\n\n\n"
               "def main(argv):\n    return COMMANDS[argv[0]](argv)\n\n\nif __name__ == '__main__':\n    main([])\n"),
    "tests/test_cli.py": "from cli import main\n\n\ndef test_graph():\n    main(['graph'])\n",
    "tests/test_cli_other.py": "from cli import main\n\n\ndef test_other():\n    main(['other'])\n",
}


def edit_core(repo):
    write_files(repo, {"core.py": f"class Core:\n{EDITED}"})


def test_diff_names_tour_steps_over_the_changed_code(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, monkeypatch, {**CORE_FILES, ".coderstanding/tours/walk.md": TOUR})
    edit_core(repo)
    assert "tour steps over this code (may go stale): walk step 1" in run(capsys, repo)


def test_diff_names_no_tour_for_code_outside_every_step(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, monkeypatch, {**CORE_FILES, ".coderstanding/tours/walk.md": TOUR})
    write_files(repo, {"lonely.py": f"class Lonely:\n{EDITED}"})
    assert not any("tour steps" in line for line in run(capsys, repo))


def test_impact_names_tour_steps(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, monkeypatch, {**CORE_FILES, ".coderstanding/tours/walk.md": TOUR})
    capsys.readouterr()
    main(["impact", "t", "Core#run", "--repo", str(repo)])
    assert "tour steps over this code (may go stale): walk step 1" in capsys.readouterr().out


def test_viewer_change_lists_the_e2e_run_and_checks_naming_the_function(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, monkeypatch, {**CORE_FILES, **VIEWER_FILES})
    write_files(repo, {"viewer/app.js": VIEWER_FILES["viewer/app.js"].replace("box.size", "box.size + 1")})
    assert ('e2e: node tests/e2e/run.mjs --only "boxes: draw"  (checks naming a changed function; '
            'leave out --only to run all)') in run(capsys, repo)


def test_viewer_change_naming_no_check_still_lists_the_whole_run(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, monkeypatch, {**CORE_FILES, **VIEWER_FILES})
    write_files(repo, {"viewer/app.js": VIEWER_FILES["viewer/app.js"].replace("return 1", "return 2")})
    assert "e2e: node tests/e2e/run.mjs  (no check names a changed function)" in run(capsys, repo)


def test_vendored_or_python_change_lists_no_e2e(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, monkeypatch, {**CORE_FILES, **VIEWER_FILES})
    write_files(repo, {"viewer/vendor/lib.js": "function vendored() { return 1; }\n"})
    edit_core(repo)
    assert not any(line.startswith("e2e:") for line in run(capsys, repo))


def test_text_helper_change_lists_tests_calling_its_command(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, monkeypatch, COMMAND_FILES)
    write_files(repo, {"texts.py": COMMAND_FILES["texts.py"].replace("' plan: {count}'", "' plan:  {count}'")})
    lines = run(capsys, repo)
    assert "tests/test_cli.py" in lines
    assert "tests/test_cli_other.py" not in lines


def test_impact_lists_tests_calling_its_command(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, monkeypatch, COMMAND_FILES)
    capsys.readouterr()
    main(["impact", "t", "texts#plan_text", "--all", "--repo", str(repo)])
    out = capsys.readouterr().out
    # plan_text -> header is a same-module call: the walk reaches the runner, so cli's tests count
    assert "1: texts#header" in out and "2: cli#run_graph" in out
    assert "tests/test_cli.py" in out
