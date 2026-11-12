"""Viewer code -> the end-to-end checks that exercise it (docs/DESIGN.md 12a).

`tests/e2e/run.mjs` runs named checks (`await step("name", ...)`); `--only WORDS` runs those whose
name contains a word. A changed viewer function maps to the checks whose code names it (the
names index lists those lines); any viewer change at all maps to the e2e run.
"""

import re

from .textindex import file_lines, uses

E2E_DIR = "tests/e2e/"
E2E_RUNNER = "tests/e2e/run.mjs"
VIEWER_DIR = "viewer/"
VIEWER_SKIP = ("viewer/vendor/", "viewer/hosted.json")
STEP_CALL = re.compile(r"""\bstep\(\s*(["'`])(.+?)\1""")
FUNCTION_DECLARATION = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)|^(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\(|\w+\s*=>)")
MAX_CHECKS_PER_NAME = 10  # a name in more checks than this is too common to pick checks by


def is_viewer_file(rel_path):
    return rel_path.startswith(VIEWER_DIR) and not rel_path.startswith(VIEWER_SKIP)


def check_at(lines, line):
    """Name of the check (`step("...")`) running at the 1-based line, or None before the first."""
    for number in range(min(line, len(lines)), 0, -1):
        match = STEP_CALL.search(lines[number - 1])
        if match:
            return match.group(2)
    return None


def only_word(check_name):
    """What `--only` takes for a check: its name up to the first comma (commas separate words)."""
    return check_name.split(",")[0].strip()


def checks_naming(index, root, function):
    """Sorted check names in tests/e2e/*.mjs whose code names the function."""
    rows = [r for r in uses(index, root, function, kinds=("test",))
            if r["file"].startswith(E2E_DIR) and not r["declares"]]
    texts = file_lines(root, {r["file"] for r in rows})
    names = {check_at(texts[r["file"]], r["line"]) for r in rows} - {None}
    return sorted(names)


def changed_function_names(root, changed):
    """Names of the functions around the changed lines of viewer files ({file: [(first, last)]}).
    The graph keeps no functions for plain JS modules, so this reads the declaration lines."""
    viewer = {f: ranges for f, ranges in changed.items() if is_viewer_file(f) and f.endswith(".js")}
    names = set()
    for file, lines in file_lines(root, viewer).items():
        for first, last in viewer[file]:
            for line in range(max(first, 1), (last or len(lines)) + 1):
                name = declared_function_above(lines, line)
                if name:
                    names.add(name)
    return names


def declared_function_above(lines, line):
    """The function declared on the 1-based line, else the nearest one above it."""
    for number in range(min(line, len(lines)), 0, -1):
        match = FUNCTION_DECLARATION.match(lines[number - 1])
        if match:
            return match.group(1) or match.group(2)
    return None


def e2e_for_diff(index, root, changed):
    """None when no viewer file changed, else {"checks": names of the checks naming a changed function}."""
    if not any(is_viewer_file(f) for f in changed):
        return None
    names = set()
    for function in changed_function_names(root, changed):
        found = checks_naming(index, root, function)
        if len(found) <= MAX_CHECKS_PER_NAME:
            names.update(found)
    return {"checks": sorted(names)}
