"""Test code (docs/DESIGN.md 12a): which nodes are tests, and which tests cover a node.

One rule for every language: a node is test code when its file is in a test folder, has a
test file name, or sits in a C# test project. `mark_tests` runs at scan time and sets the
`test` flag; the view engine hides flagged nodes unless asked.
"""

import posixpath
from fnmatch import fnmatch

from .model import called_function, calling_function

TEST_FLAG = "test"
TEST_DIRS = ("test", "tests", "__tests__")
TEST_DIR_PATTERNS = ("*.Tests", "*.Test", "*.UnitTests", "*.IntegrationTests")  # C# projects
# test_*.py, GUT test_*.gd, foo_test.go, foo.test.ts, foo.spec.js, FooTests.cs
TEST_FILE_PATTERNS = ("test_*.*", "*_test.*", "*.test.*", "*.spec.*", "*Tests.cs")
TEST_PACKAGES = ("xunit", "nunit", "mstest.testframework", "microsoft.net.test.sdk")


# Function names in a test file, compared without case and underscores (before_each, beforeEach,
# setUp, SetUp ...): test functions, framework hooks, and lifecycle names that are neither.
HOOK_NAMES = frozenset({"beforeall", "beforeeach", "aftereach", "afterall", "setup", "teardown",
                        "setupclass", "teardownclass", "setupmodule", "teardownmodule",
                        "onetimesetup", "onetimeteardown"})
LIFECYCLE_NAMES = frozenset({"init", "ready", "new"})


def _plain(name):
    return name.replace("_", "").lower()


def function_role(name):
    """`test`, `hook` (runs around tests), or `helper` (everything else a test file defines)."""
    plain = _plain(name)
    if plain.startswith("test"):
        return "test"
    return "hook" if plain in HOOK_NAMES else "helper"


def is_helper_function(name):
    return function_role(name) == "helper" and _plain(name) not in LIFECYCLE_NAMES


def is_test_dir(name):
    return name in TEST_DIRS or any(fnmatch(name, p) for p in TEST_DIR_PATTERNS)


def is_test_path(rel_path, test_dirs=()):
    """True for a repo-relative file path that holds test code.
    test_dirs: extra folders known to be test projects (C# projects using a test framework)."""
    folders, name = rel_path.split("/")[:-1], posixpath.basename(rel_path)
    if any(is_test_dir(f) for f in folders) or any(fnmatch(name, p) for p in TEST_FILE_PATTERNS):
        return True
    return any(not d or rel_path.startswith(d + "/") for d in test_dirs)


def is_runnable_test(rel_path):
    """A test file a runner is pointed at: a Python file in a test folder must also have a test
    file name (so `tests/helpers.py` and `conftest.py` are support code, not tests to run)."""
    name = posixpath.basename(rel_path)
    return not name.endswith(".py") or any(fnmatch(name, p) for p in TEST_FILE_PATTERNS)


def find_test_projects(stack):
    """Folders of C# projects that reference a test framework (from the stack reader)."""
    dirs = []
    for project in stack.get("dotnet", []):
        names = {p["name"].lower() for p in project.get("packages", [])}
        if names & set(TEST_PACKAGES):
            dirs.append(posixpath.dirname(project["file"]))
    return dirs


def mark_tests(graph):
    """Flag every in-repo node whose file is test code. Run after the stack reader."""
    dirs = find_test_projects(graph.stack)
    for node in graph.nodes.values():
        if node.kind != "external" and TEST_FLAG not in node.flags \
                and is_test_path(node.file, dirs):
            node.flags.append(TEST_FLAG)


def is_test(node):
    return node is not None and TEST_FLAG in node.flags


def enclosing_ids(graph, node_id):
    """The node and the nodes it is nested in (`pkg.mod.Class` -> `pkg.mod`), in the graph."""
    ids, current = [node_id], node_id
    while "." in current:
        current = current.rpartition(".")[0]
        if current in graph.nodes:
            ids.append(current)
    return ids


LANGUAGE_MODULE_BASE = "LanguageModule"  # the base class every language reader extends (language.py)


def language_module_files(graph):
    """node id -> file, for each language module (a class extending `LanguageModule`)."""
    return {edge.from_id: graph.nodes[edge.from_id].file for edge in graph.edges.values()
            if edge.kind == "generalization" and edge.from_id in graph.nodes
            and graph.nodes[edge.from_id].file and graph.nodes.get(edge.to)
            and graph.nodes[edge.to].name == LANGUAGE_MODULE_BASE}


def module_cover(graph, module_files):
    """language module id -> ids a test using the module reaches: the nodes of the module's file
    and the non-test nodes those use (helpers the readers share, e.g. `languages/stubs.py`)."""
    nodes_by_file = {}
    for node in graph.nodes.values():
        nodes_by_file.setdefault(node.file, []).append(node.id)
    own = {module_id: set(nodes_by_file.get(file, ())) for module_id, file in module_files.items()}
    cover = {module_id: set(ids) for module_id, ids in own.items()}
    for edge in graph.edges.values():
        if is_test(graph.nodes.get(edge.to)):
            continue
        for module_id, ids in own.items():
            if edge.from_id in ids:
                cover[module_id].add(edge.to)
    return cover


def index_tests(graph):
    """target id -> set of test files with an edge to it. A test that uses a language module
    (`scan_repo(modules=[KotlinModule()])`) also covers the other code in that module's file
    and what that code uses, which it reaches only through the module."""
    index = {}
    cover = module_cover(graph, language_module_files(graph))
    for edge in graph.edges.values():
        source = graph.nodes.get(edge.from_id)
        if is_test(source) and is_runnable_test(source.file) and not is_test(graph.nodes.get(edge.to)):
            for node_id in cover.get(edge.to, [edge.to]):
                index.setdefault(node_id, set()).add(source.file)
    return index


def covering_tests(graph, node_id, index=None):
    """Sorted test files with a line to the node or to a node it is nested in."""
    index = index_tests(graph) if index is None else index
    return sorted({f for i in enclosing_ids(graph, node_id) for f in index.get(i, ())})


def function_test_index(graph):
    """(node id, function name) -> set of test files whose calls land on that function by name:
    the one rule for "a test calls this function" (direct_tests, progress tracks)."""
    index = {}
    for edge in graph.edges.values():
        source = graph.nodes.get(edge.from_id)
        if is_test(source) and is_runnable_test(source.file):
            for call in edge.calls:
                index.setdefault((edge.to, called_function(call.member)), set()).add(source.file)
    return index


def direct_tests(graph, node_id, function):
    """Sorted test files whose calls land on the function by name (none when nobody calls it so)."""
    return sorted(function_test_index(graph).get((node_id, function), ()))


def tests_using(graph, node_id, function=None):
    """Sorted test files using a class, or a function of it: those whose calls land on the
    function, else (none call it by name) the tests covering its class."""
    return (function and direct_tests(graph, node_id, function)) or covering_tests(graph, node_id)


def users(graph, node_id):
    """Non-test nodes with an edge to node_id."""
    return sorted({e.from_id for e in graph.edges.values()
                   if e.to == node_id and e.from_id != node_id
                   and not is_test(graph.nodes.get(e.from_id))})


def function_callers(graph, node_id, function):
    """[(non-test node id, its calling function or None)] with a recorded call to the function."""
    return sorted({(e.from_id, calling_function(c.from_member)) for e in graph.edges.values()
                   if e.to == node_id and e.from_id != node_id
                   and not is_test(graph.nodes.get(e.from_id))
                   for c in e.calls if called_function(c.member) == function},
                  key=lambda caller: (caller[0], caller[1] or ""))


def retest_files(graph, node_id, user_ids=()):
    """[(test file, via user id or "")]: the node's own tests first (none for node_id None),
    then those of each user."""
    index = index_tests(graph)
    found = {f: "" for f in covering_tests(graph, node_id, index)} if node_id else {}
    for user in user_ids:
        for test_file in covering_tests(graph, user, index):
            found.setdefault(test_file, user)
    return sorted(found.items(), key=lambda item: (item[1] != "", item[0]))


def files_to_retest(graph, node_id, deep=False):
    """[(test file, via user id or "")]: direct tests first; deep adds tests of direct users."""
    return retest_files(graph, node_id, users(graph, node_id) if deep else ())
