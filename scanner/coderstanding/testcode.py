"""Test code (docs/DESIGN.md 12a): which nodes are tests, and which tests cover a node.

One rule for every language: a node is test code when its file is in a test folder, has a
test file name, or sits in a C# test project. `mark_tests` runs at scan time and sets the
`test` flag; the view engine hides flagged nodes unless asked.
"""

import posixpath
from fnmatch import fnmatch

TEST_FLAG = "test"
TEST_DIRS = ("test", "tests", "__tests__")
TEST_DIR_PATTERNS = ("*.Tests", "*.Test", "*.UnitTests", "*.IntegrationTests")  # C# projects
# test_*.py, GUT test_*.gd, foo_test.go, foo.test.ts, foo.spec.js, FooTests.cs
TEST_FILE_PATTERNS = ("test_*.*", "*_test.*", "*.test.*", "*.spec.*", "*Tests.cs")
TEST_PACKAGES = ("xunit", "nunit", "mstest.testframework", "microsoft.net.test.sdk")


def is_test_dir(name):
    return name in TEST_DIRS or any(fnmatch(name, p) for p in TEST_DIR_PATTERNS)


def is_test_path(rel_path, test_dirs=()):
    """True for a repo-relative file path that holds test code.
    test_dirs: extra folders known to be test projects (C# projects using a test framework)."""
    folders, name = rel_path.split("/")[:-1], posixpath.basename(rel_path)
    if any(is_test_dir(f) for f in folders) or any(fnmatch(name, p) for p in TEST_FILE_PATTERNS):
        return True
    return any(not d or rel_path.startswith(d + "/") for d in test_dirs)


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


def index_tests(graph):
    """target id -> set of test files with an edge to it."""
    index = {}
    for edge in graph.edges.values():
        source = graph.nodes.get(edge.from_id)
        if is_test(source) and not is_test(graph.nodes.get(edge.to)):
            index.setdefault(edge.to, set()).add(source.file)
    return index


def covering_tests(graph, node_id, index=None):
    """Sorted test files with a line to the node or to a node it is nested in."""
    index = index_tests(graph) if index is None else index
    return sorted({f for i in enclosing_ids(graph, node_id) for f in index.get(i, ())})


def users(graph, node_id):
    """Non-test nodes with an edge to node_id."""
    return sorted({e.from_id for e in graph.edges.values()
                   if e.to == node_id and e.from_id != node_id
                   and not is_test(graph.nodes.get(e.from_id))})


def retest_files(graph, node_id, user_ids=()):
    """[(test file, via user id or "")]: the node's own tests first, then those of each user."""
    index = index_tests(graph)
    found = {f: "" for f in covering_tests(graph, node_id, index)}
    for user in user_ids:
        for test_file in covering_tests(graph, user, index):
            found.setdefault(test_file, user)
    return sorted(found.items(), key=lambda item: (item[1] != "", item[0]))


def files_to_retest(graph, node_id, deep=False):
    """[(test file, via user id or "")]: direct tests first; deep adds tests of direct users."""
    return retest_files(graph, node_id, users(graph, node_id) if deep else ())
