"""The contract every language module implements, and the registry modules join.

A language module turns one file into a FileResult: declarations (future nodes) and raw
references (future edges). It never resolves names or builds edges itself; the shared
resolver does that. See docs/AGENTS.md "How to write a language module".
"""

import contextlib
import importlib
import pkgutil
import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
# Ref.from_member values (initializers/top-level code, a field's type), re-exported for readers
from .model import FIELD_MEMBER, INIT_MEMBER  # noqa: F401


@dataclass(frozen=True)
class Target:
    """What a reference points at. Set exactly one of name / path / id.

    name:   a type name as written ("Foo", "Outer.Inner", "pkg.mod.Foo"); resolved through
            file aliases, global aliases, the ref's scope chain, imports, then globally.
    path:   a file as written ("res://x.gd", "./util", "src/a.py"); resolves to the node
            that declared itself the file's node (Declaration.file_node).
    id:     an exact node id, when the module already knows it.
    member: optional child name looked up under the resolved node ("Foo" in
            `import {Foo} from './a'`); falls back to the node itself if there is no child.
    scope:  for a name: the dotted scope it was written in, used when the resolving Ref has
            none (alias targets: C++ `using V = Vec<float>;` inside `namespace a`).
    """
    name: str = ""
    path: str = ""
    id: str = ""
    member: str = ""
    scope: str = ""

    @property
    def text(self):
        base = self.name or self.path or self.id
        return f"{base}.{self.member}" if self.member else base


@dataclass
class Ref:
    """One raw reference from a declared type to something else."""
    from_id: str
    to: Target
    kind: str  # one of model.EDGE_KINDS
    line: int = 0
    detail: str = ""  # field, export, call, create, param, return, local, load, import, ...
    call: str = ""  # member called on the target, if any
    scope: str = ""  # dotted enclosing scope for name lookup (C#: "NS.Outer"), "" = global
    auto: str = ""  # auto text for the edge ("signal hit -> _on_hit")
    from_member: str = None  # enclosing function/method name, INIT_MEMBER, FIELD_MEMBER; None = n/a


@dataclass
class Declaration:
    """One type the file declares; becomes a node (same ids merge, e.g. partial classes)."""
    id: str
    name: str
    kind: str  # one of model.NODE_KINDS
    line: int = 1
    flags: list = field(default_factory=list)
    namespace: str = ""  # namespace / module / package, info only
    base: str = ""  # first base as written ("Node3D"), info only; base edges are Refs
    members: list = field(default_factory=list)  # model.Member
    file_node: bool = False  # True for the node a path reference to this file means
    lang: str = ""  # defaults to the module's name
    end_line: int = 0  # last line of the type, when the reader knows it (size); 0 = unknown
    doc: str = ""  # header comment of the type / file, already trimmed (languages/doc_text.py)


@dataclass
class Mark:
    """Flags to add to a node that may be declared in another file (Godot autoloads)."""
    target: Target
    flags: list


@dataclass
class FileResult:
    declarations: list = field(default_factory=list)
    refs: list = field(default_factory=list)
    imports: list = field(default_factory=list)  # prefixes tried after the scope chain (C# usings)
    aliases: dict = field(default_factory=dict)  # file-local name -> Target (preload consts, imports)
    global_aliases: dict = field(default_factory=dict)  # name -> Target for every file (autoloads)
    marks: list = field(default_factory=list)  # Mark


@dataclass
class FileContext:
    """What a module gets for one file."""
    root: Path
    rel_path: str  # repo-relative POSIX path

    @property
    def path(self):
        return self.root / self.rel_path

    def read_text(self):
        return self.path.read_text(encoding="utf-8", errors="replace")

    def read_bytes(self):
        return self.path.read_bytes()

    def file_exists(self, rel_path):
        """True when the repo file exists. A missing one is recorded as a dependency, so the
        scan cache re-parses this file once it appears."""
        if (self.root / rel_path).is_file():
            return True
        record_dependency(rel_path)
        return False

    def godot_root(self):
        return paths.godot_root(str(self.root), posixpath.dirname(self.rel_path))

    def res_path(self):
        """This file as a res:// path (repo root stands in when there is no project.godot)."""
        return paths.rel_to_res(self.rel_path, self.godot_root())


_dependency_sets = []  # one set per active track_dependencies()


@contextlib.contextmanager
def track_dependencies():
    """Collect the repo-relative paths that record_dependency() reports inside the block."""
    found = set()
    _dependency_sets.append(found)
    try:
        yield found
    finally:
        _dependency_sets.remove(found)


def record_dependency(*rel_paths):
    """A module calls this when its result also depends on other files' content.

    The scan cache then re-parses the file when any of those files changes (or appears).
    """
    for found in _dependency_sets:
        found.update(rel_paths)


class LanguageModule:
    """Base class for language modules. Subclass, set the attributes, implement parse()."""
    name = ""  # short language name, also written as node "lang"
    patterns = ()  # file globs: matched against the file name, or the path if they hold "/"
    path_suffixes = ("",)  # tried after a path reference ("", ".ts", "/index.js", ...)
    loose_names = False  # last resort: match a name against any node id ending in ".Name"

    def parse(self, ctx):
        """Return a FileResult for one file (ctx is a FileContext)."""
        raise NotImplementedError

    def start_scan(self):
        """Called once before each scan; drop any in-memory state from earlier scans."""



class Registry:
    def __init__(self):
        self._modules = {}

    def register(self, module_class):
        """Class decorator: @registry.register on a LanguageModule subclass."""
        module = module_class()
        if not module.name or not module.patterns:
            raise ValueError(f"{module_class.__name__} needs a name and patterns")
        if module.name in self._modules:
            raise ValueError(f"language module {module.name!r} registered twice")
        self._modules[module.name] = module
        return module_class

    def modules(self):
        return [self._modules[name] for name in sorted(self._modules)]


registry = Registry()
register = registry.register


def load_builtin_modules():
    """Import every module in coderstanding.languages so they register themselves."""
    from . import languages
    for info in pkgutil.iter_modules(languages.__path__):
        importlib.import_module(f"{languages.__name__}.{info.name}")
    return registry.modules()
