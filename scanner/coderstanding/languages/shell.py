"""Shell reader: a module node per script (`*.sh`, `*.bash`), its functions as members.

Calls between the script's functions, `source` / `.` of another repo script (an import) and
running another repo script (`./x.sh`, `bash tools/y.sh`, `"$DIR/z.sh"`) become dependency refs
to that script's node. Outside commands (`git`, `docker`) are plain refs the stack reader counts
as platform names. Env vars are read by the names index (`textindex.SHELL_ENV`), not here.
"""

import posixpath
import re

import tree_sitter_bash

from coderstanding.language import (INIT_MEMBER, Declaration, FileResult, LanguageModule, Ref, Target,
                                   register)
from coderstanding.model import Member, entry_flags

from . import doc_text
from .stubs import is_stub
from .ts_util import children_of_type, descendants, end_line, first_error_line, line, make_parser, text

PARSER = make_parser(tree_sitter_bash.language())
DYNAMIC = "\0"  # stands for a `$VAR`, `${...}` or `$(...)` in a path written in the script
DYNAMIC_TYPES = ("simple_expansion", "expansion", "command_substitution", "arithmetic_expansion")
NOT_ARGUMENT_TYPES = ("variable_assignment", "file_redirect", "heredoc_redirect", "herestring_redirect",
                      "comment")
IMPORT_COMMANDS = ("source", ".")
INTERPRETERS = ("bash", "sh", "zsh", "dash", "ksh", "python", "python3", "node", "ruby", "perl")
WRAPPERS = ("sudo", "exec", "nohup", "time", "nice", "ionice", "env", "command")
# Only these files are linked: an unscanned one (a bare `bin/tool`) would be an unresolved name.
SCRIPT_SUFFIXES = (".sh", ".bash", ".py", ".js", ".mjs", ".cjs", ".ts", ".gd")
STOP_FLAGS = ("-c", "-m", "-e")  # the next word is code or a module, not a script path
PLAIN_COMMAND = re.compile(r"^[A-Za-z][\w+.-]*$")
# The grammar reads `A= B=` + newline + `while ...` as one command, so keywords can show up as names.
KEYWORDS = {"if", "then", "else", "elif", "fi", "for", "in", "while", "until", "do", "done", "case", "esac",
            "select", "function", "coproc"}
BUILTINS = KEYWORDS | {
    "echo", "printf", "cd", "pwd", "test", "true", "false", "exit", "return", "export", "local", "declare",
    "readonly", "unset", "set", "shift", "read", "eval", "trap", "wait", "kill", "jobs", "fg", "bg", "alias",
    "unalias", "type", "builtin", "hash", "let", "mapfile", "readarray", "getopts", "break", "continue",
    "umask", "ulimit", "pushd", "popd", "dirs", "typeset", "shopt", "complete", "compgen", "disown", "caller",
    "enable", "help", "history", "bind", "times", "suspend", "logout", "fc", *WRAPPERS,
}


def parse_source(source):
    return PARSER.parse(source).root_node


# ---- words and paths --------------------------------------------------------------------

def literal(node):
    """A word, quoted string or concatenation as written, with DYNAMIC where a variable or
    command substitution stands."""
    if node.type in DYNAMIC_TYPES:
        return DYNAMIC
    if node.type == "raw_string":
        return text(node)[1:-1]
    if node.type in ("command_name", "string", "concatenation"):
        return "".join(literal(child) for child in node.children if child.type != '"')
    return text(node)


def words_of(command):
    """The command's words (name first), as (literal, node); redirects and `X=1` prefixes left out."""
    nodes = [child for child in command.children if child.type not in NOT_ARGUMENT_TYPES]
    return [(literal(child), child) for child in nodes if child.is_named]


def without_wrappers(words):
    """`sudo -n nice -n 19 foo a` -> the words of `foo a`."""
    while words and words[0][0] in WRAPPERS:
        words = words[1:]
        while words and (words[0][0].startswith("-") or words[0][0].isdigit() or "=" in words[0][0]):
            words = words[1:]
    return words


def script_argument(words):
    """The script an interpreter runs (`bash tools/y.sh`): its first word that is not a flag."""
    for written, _ in words[1:]:
        if written in STOP_FLAGS:
            return ""
        if not written.startswith("-"):
            return written
    return ""


def enclosing_function(node):
    while node is not None and node.type != "function_definition":
        node = node.parent
    return text(node.child_by_field_name("name")) if node is not None else INIT_MEMBER


def function_name(node):
    return text(node.child_by_field_name("name"))


def function_stub(node):
    body = node.child_by_field_name("body")
    return is_stub(body, node)


class ShellFile:
    """Reads one script into a FileResult."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.module_id = ctx.rel_path
        self.result = FileResult()
        self.functions = set()

    def read(self):
        source = self.ctx.read_bytes()
        root = parse_source(source)
        module = Declaration(self.module_id, posixpath.basename(self.module_id), "module", 1,
                             namespace=posixpath.dirname(self.module_id), file_node=True,
                             end_line=end_line(root), doc=doc_text.file_doc(root, ()))
        if source.startswith(b"#!"):  # a script to run, not only a library to source
            module.flags += entry_flags("script-main")
        self.result.declarations.append(module)
        self.result.parse_error_line = first_error_line(root)
        functions = [n for n in descendants(root) if n.type == "function_definition"]
        self.functions = {function_name(n) for n in functions}
        module.members = [Member(function_name(n), "function", line=line(n), end=end_line(n),
                                 stub=function_stub(n)) for n in functions]
        for command in (n for n in descendants(root) if n.type == "command"):
            self.read_command(command)
        return self.result

    # --- commands ---------------------------------------------------------------------

    def read_command(self, command):
        member = enclosing_function(command)
        words = words_of(command)
        if not words or words[0][1].type != "command_name":
            return
        words = without_wrappers(words)
        if not words:
            return
        name = words[0][0]
        if name in self.functions:
            self.result.refs.append(Ref.own_call(self.module_id, name, line(command), member))
        elif name in IMPORT_COMMANDS:
            self.add_script_ref(words[1][0] if len(words) > 1 else "", command, "import", member)
        elif name in INTERPRETERS:
            self.add_script_ref(script_argument(words), command, "call", member)
            self.add_outside(name, command, member)
        elif "/" in name or name.startswith(DYNAMIC):
            self.add_script_ref(name, command, "call", member)
        elif PLAIN_COMMAND.match(name) and name not in BUILTINS:
            self.add_outside(name, command, member)

    def add_ref(self, target, node, detail, member, call=""):
        self.result.refs.append(Ref(self.module_id, target, "dependency", line(node), detail, call,
                                    from_member=member))

    def add_outside(self, name, node, member):
        self.add_ref(Target(name=name), node, "call", member)

    def add_script_ref(self, written, node, detail, member):
        rel = self.repo_script(written)
        if rel and rel != self.module_id:
            self.add_ref(Target(path=rel), node, detail, member)

    def repo_script(self, written):
        """The repo file a script path written in this script means, or "" (not literal, not a
        repo file). A leading `$DIR/` is the script's folder or the repo root: both are tried."""
        if not written or DYNAMIC in written[1:]:
            return ""
        has_prefix = written.startswith(DYNAMIC)
        path = written[1:].lstrip("/") if has_prefix else written
        if not path.endswith(SCRIPT_SUFFIXES) or path.startswith(("/", "~")) or (has_prefix and not written[1:].startswith("/")):
            return ""
        for folder in (posixpath.dirname(self.module_id), ""):
            rel = posixpath.normpath(posixpath.join(folder, path))
            if not rel.startswith("../") and self.ctx.file_exists(rel):
                return rel
        return ""


@register
class ShellModule(LanguageModule):
    name = "shell"
    patterns = ("*.sh", "*.bash")

    def parse(self, ctx):
        return ShellFile(ctx).read()
