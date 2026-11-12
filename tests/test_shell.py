from coderstanding.languages.shell import ShellModule
from coderstanding.scan import scan_repo
from coderstanding.stack import is_unresolved
from coderstanding.textindex import env_rules, env_hits
from helpers import edge, write_files

MAIN = """#!/usr/bin/env bash
# Builds the thing.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib.sh"
. ./scripts/common.sh
export WOOF_X=1

helper() { echo hi; }
function other {
  helper
  git status
}
stub() { :; }
todo() { echo "not implemented"; }
./scripts/x.sh --a
bash tools/y.sh
"$DIR/scripts/z.sh" arg
sudo -n nice -n 19 bash tools/y.sh
bash -c "tools/nope.sh"
./missing.sh
if helper; then docker ps | grep x; fi
echo "$WOOF_Y" "$(helper)"
"""
FILES = {
    "run.sh": MAIN,
    "lib.sh": "lib_fn() { :; }\n",
    "scripts/common.sh": "# shared\n",
    "scripts/x.sh": "#!/bin/sh\nsource ../lib.sh\n",
    "scripts/z.sh": "echo z\n",
    "tools/y.sh": "#!/bin/bash\nexit 0\n",
}


def scan(tmp_path):
    write_files(tmp_path, FILES)
    return scan_repo(tmp_path, modules=[ShellModule()])


def test_script_is_a_module_with_functions(tmp_path):
    graph = scan(tmp_path)
    node = graph.nodes["run.sh"]
    assert (node.kind, node.lang, node.doc) == ("module", "shell", "Builds the thing.")
    assert "script-main" in node.flags and "entry" in node.flags
    assert "script-main" not in graph.nodes["scripts/common.sh"].flags  # no shebang: a library
    members = {m.name: m for m in node.members}
    assert set(members) == {"helper", "other", "stub", "todo"}
    assert (members["other"].line, members["other"].end) == (10, 13)
    assert [name for name, m in members.items() if m.stub] == ["stub", "todo"]


def test_calls_between_functions(tmp_path):
    graph = scan(tmp_path)
    found = {(c.from_member, c.member): c.count for c in edge(graph, "run.sh", "run.sh", "dependency").calls}
    assert found == {("other", "helper"): 1, ("<init>", "helper"): 2}  # top-level code is <init>


def test_source_and_run_other_scripts(tmp_path):
    graph = scan(tmp_path)
    assert "import" in edge(graph, "run.sh", "lib.sh", "dependency").detail
    assert "import" in edge(graph, "run.sh", "scripts/common.sh", "dependency").detail
    assert "import" in edge(graph, "scripts/x.sh", "lib.sh", "dependency").detail  # ../lib.sh
    for target in ("scripts/x.sh", "scripts/z.sh", "tools/y.sh"):
        assert "call" in edge(graph, "run.sh", target, "dependency").detail
    assert len(edge(graph, "run.sh", "tools/y.sh", "dependency").at) == 2  # also behind sudo nice


def test_outside_commands_are_platform_names_not_warnings(tmp_path):
    node = scan(tmp_path).nodes["run.sh"]
    names = {edge.to for edge in node.external_refs.values()}
    assert {"git", "docker", "grep", "bash"} <= names
    assert "echo" not in names and "helper" not in names and "./missing.sh" not in names
    assert not [name for name in names if is_unresolved(node, name)]


def test_shell_scripts_read_env_the_shell_way():
    hits = dict(env_hits(env_rules("run.sh", True), 'export WOOF_X=1; echo "$WOOF_Y"'))
    assert hits == {"WOOF_X": "set", "WOOF_Y": "read"}


def test_keywords_the_grammar_reads_as_commands_are_not_outside_commands(tmp_path):
    """tree-sitter-bash can read a loop keyword as a command (seen in a project's run_tests.sh)."""
    write_files(tmp_path, {"a.sh": "echo hi\ndone\ndo\ngit status\n"})
    node = scan_repo(tmp_path, modules=[ShellModule()]).nodes["a.sh"]
    assert {edge.to for edge in node.external_refs.values()} == {"git"}
