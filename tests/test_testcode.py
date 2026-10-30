import pytest

from coderstanding.levels import guess_levels
from coderstanding.testcode import (covering_tests, find_test_projects, is_test_path, mark_tests,
                                    files_to_retest)
from helpers import scan_fake


@pytest.mark.parametrize("path", [
    "tests/test_views.py", "test/a.gd", "src/__tests__/a.js", "src/test_net.gd",
    "pkg/net_test.go", "web/app.test.ts", "web/app.spec.js", "Game.Tests/PlayerTests.cs",
    "src/PlayerTests.cs", "deep/tests/helpers/x.py",
])
def test_test_paths(path):
    assert is_test_path(path)


@pytest.mark.parametrize("path", [
    "src/net.gd", "src/testing.py", "src/contest.py", "src/latest_news.js", "src/Tester.cs",
    "attest/a.py",
])
def test_code_paths(path):
    assert not is_test_path(path)


def test_csharp_test_project_by_package():
    stack = {"dotnet": [
        {"file": "Checks/Checks.csproj", "packages": [{"name": "xunit", "version": "2"}]},
        {"file": "Game/Game.csproj", "packages": [{"name": "Newtonsoft.Json", "version": "13"}]},
    ]}
    dirs = find_test_projects(stack)
    assert dirs == ["Checks"]
    assert is_test_path("Checks/Rules.cs", dirs) and not is_test_path("Game/Rules.cs", dirs)


GAME = {
    "net.fk": "type Net class\n",
    "lobby.fk": "type Lobby class\nref dependency Net call\n",
    "tests/test_net.fk": "type TestNet class\nref dependency Net call\n",
    "tests/test_lobby.fk": "type TestLobby class\nref dependency Lobby call\n",
    "mod.fk": "type mod module\ntype mod.Inner class\n",
    "tests/test_mod.fk": "type TestMod class\nref dependency mod\n",
}


def test_scan_marks_tests(tmp_path):
    graph = scan_fake(tmp_path, GAME)
    flagged = {i for i, n in graph.nodes.items() if "test" in n.flags}
    assert flagged == {"TestNet", "TestLobby", "TestMod"}


def test_tested_by_direct_and_enclosing(tmp_path):
    graph = scan_fake(tmp_path, GAME)
    assert covering_tests(graph, "Net") == ["tests/test_net.fk"]
    assert covering_tests(graph, "mod.Inner") == ["tests/test_mod.fk"]  # via its module
    assert covering_tests(graph, "TestNet") == []


def test_tests_to_run_deep(tmp_path):
    graph = scan_fake(tmp_path, GAME)
    assert files_to_retest(graph, "Net") == [("tests/test_net.fk", "")]
    assert files_to_retest(graph, "Net", deep=True) == [
        ("tests/test_net.fk", ""), ("tests/test_lobby.fk", "Lobby")]


def test_tests_are_never_level_one_roots(tmp_path):
    graph = scan_fake(tmp_path, {
        "a.fk": "type A class\nref composition B field\n", "b.fk": "type B class\n",
        "tests/test_b.fk": "type TestB class\nref composition B field\n"})
    mark_tests(graph)
    guess_levels(graph)
    assert graph.nodes["A"].level == 1 and graph.nodes["B"].level == 2
