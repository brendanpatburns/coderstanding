"""Tests that reach code through a CLI command the graph has no edge for (docs/DESIGN.md 12a).

A CLI runner dispatches commands by name (`set_defaults(run=run_graph)`), so no call reaches
`run_graph` from `main`. From a changed function we walk its callers (views.ripple, same-module
calls included) up to a command runner (`run_graph` in an entry module), then list the tests that
call that module with the command's name quoted (`main(["graph", ...])`).
"""

import re

from .testcode import is_runnable_test
from .textindex import file_lines
from .views import ripple

RUNNER_NAME = re.compile(r"^(?:run|cmd|command)_(\w+)$")
CLIMB_DEPTH = 4  # how many callers-of-callers to follow
ENTRY_FLAG = "entry"


def reached_commands(graph, unit):
    """{(entry node id, command)} of the runners (`run_<command>` in an entry node) above the unit."""
    commands = set()
    for node_id, function in {unit, *(u for level in ripple(graph, {unit}, CLIMB_DEPTH) for u in level)}:
        match = RUNNER_NAME.match(function or "")
        if match and ENTRY_FLAG in graph.nodes[node_id].flags:
            commands.add((node_id, match.group(1)))
    return commands


def command_tests(graph, root, unit, test_index):
    """Sorted test files that reach an entry node (test_index: testcode.index_tests) and quote the
    command of a runner above the unit."""
    found = set()
    for entry_id, command in reached_commands(graph, unit):
        quoted = (f'"{command}"', f"'{command}'")
        files = [f for f in test_index.get(entry_id, ()) if is_runnable_test(f)]
        for test_file, lines in file_lines(root, files).items():
            if any(quote in line for line in lines for quote in quoted):
                found.add(test_file)
    return sorted(found)
