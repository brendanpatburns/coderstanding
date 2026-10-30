"""Shared name resolution (docs/DESIGN.md 4.1): declarations + raw refs -> Graph.

Order for a name: file aliases, global aliases, scope chain (innermost first; nodes, then
global aliases with the scoped name), imports,
global id, longest existing dotted prefix, then (loose modules only) any id ending in
".Name". Several matches at one step -> first match, unsure. No match -> external ref.

A path tries the module's path map first (tsconfig aliases), then the path as written, each
with the module's path suffixes, then again without the written extension (`./x.js` is
`./x.ts`). A member of a file node is its child, else a name the file exports (re-exports,
default export, `export *`), followed through barrel files.

Classic browser scripts loaded by one page share their top-level names: a `shared` ref (a
bare name the reader could not place) resolves only to a `page_globals` name of another script
on a page that loads this file, else it is dropped.
"""

import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .model import STRUCT_KINDS, Call, Edge, Graph, Location, Node, child_id


@dataclass
class Parsed:
    """One file's FileResult plus where it came from."""
    rel_path: str
    module: object  # LanguageModule
    result: object  # FileResult


@dataclass(frozen=True)
class PathMiss:
    """A path no file matched (for `coderstanding diagnose`)."""
    written: str
    from_file: str
    bases: list  # repo-relative bases tried (path map ones first)
    matched: bool  # a path map alias (tsconfig `paths`, workspace package) matched it


@dataclass(frozen=True)
class Resolution:
    id: str = ""  # node id, "" when the target is outside the repo
    label: str = ""  # outside name, used when id is ""
    unsure: bool = False


@dataclass
class Resolver:
    root: object
    graph: Graph
    file_nodes: dict = field(default_factory=dict)  # rel_path -> node id
    global_aliases: dict = field(default_factory=dict)  # name -> (Target, declaring Parsed)
    path_maps: dict = field(default_factory=dict)  # module name -> its read_path_map()
    file_parsed: dict = field(default_factory=dict)  # file node id -> Parsed (its exports)
    path_misses: dict = field(default_factory=dict)  # (module, written, from file) -> PathMiss
    parsed_by_path: dict = field(default_factory=dict)  # rel_path -> Parsed
    page_siblings: dict = field(default_factory=dict)  # script rel_path -> other scripts on its pages
    _following: set = field(default_factory=set)  # (node id, member) being followed
    _exported_cache: dict = field(default_factory=dict)  # (node id, member) -> id or None

    def resolve(self, target, parsed, scope="", use_aliases=True):
        resolution = self._resolve_base(target, parsed, scope or target.scope, use_aliases)
        if target.member and resolution.id:
            return self._with_member(resolution, target.member)
        if target.member and not target.path:  # an unresolved file keeps its path as label
            return Resolution(label=target.text)
        return resolution

    def resolve_shared(self, target, parsed):
        """A bare name as a global of a classic script sharing a page with `parsed`; the
        Resolution, or None when no such script declares it."""
        head, _, rest = target.name.partition(".")
        found = []
        for sibling in self.page_siblings.get(parsed.rel_path, ()):
            owner = self.parsed_by_path.get(sibling)
            declared = owner and owner.result.page_globals.get(head)
            resolved = declared and self.resolve(declared, owner, use_aliases=False)
            if resolved and resolved.id:
                found.append(resolved.id)
        if not found:
            return None
        node_id = found[0]
        if rest:
            node_id = self._member_of(node_id, rest) or node_id
        return Resolution(node_id, unsure=len(set(found)) > 1)

    def link_pages(self, parsed_files):
        """Scripts loaded by the same page see each other's page globals."""
        self.parsed_by_path = {p.rel_path: p for p in parsed_files}
        for page in parsed_files:
            scripts = list(dict.fromkeys(page.result.page_scripts))
            for script in scripts:
                siblings = self.page_siblings.setdefault(script, [])
                siblings.extend(s for s in scripts if s != script and s not in siblings)

    def _resolve_base(self, target, parsed, scope, use_aliases):
        if target.id:
            return self._known(target.id) or Resolution(label=target.id)
        if target.path:
            return self._resolve_path(target.path, parsed)
        return self._resolve_name(target.name, parsed, scope, use_aliases)

    def _known(self, node_id):
        return Resolution(node_id) if node_id in self.graph.nodes else None

    def _with_member(self, resolution, member):
        found = self._member_of(resolution.id, member)
        return Resolution(found or resolution.id, unsure=resolution.unsure)

    def _member_of(self, node_id, member):
        """The child `member` of a node, or the node a file node exports under that name
        (followed through re-exports); None when neither is found."""
        found = self._known(child_id(node_id, member))
        if found:
            return found.id
        parsed = self.file_parsed.get(node_id)
        key = (node_id, member)
        if parsed is None or key in self._following:
            return None
        if key in self._exported_cache:
            return self._exported_cache[key]
        self._following.add(key)
        try:
            found = self._exported(parsed, member)
        finally:
            self._following.discard(key)
        if not self._following:  # inside a loop of re-exports a miss is not final
            self._exported_cache[key] = found
        return found

    def _exported(self, parsed, member):
        target = parsed.result.exports.get(member)
        if target is not None:
            return self.resolve(target, parsed).id or None
        for written in parsed.result.star_exports:
            source = self._resolve_path(written, parsed)
            found = source.id and self._member_of(source.id, member)
            if found:
                return found
        return None

    def _resolve_path(self, written, parsed):
        rel = paths.normalize_ref_path(written, parsed.rel_path, self.root)
        path_map = self.path_maps.get(parsed.module.name)
        mapped, matched = path_map.bases(written, parsed.rel_path) if path_map else ([], False)
        bases = mapped + [rel]
        for base in bases:
            node_id = self._file_node(base, parsed.module.path_suffixes)
            if node_id:
                return Resolution(node_id)
        self.path_misses.setdefault((parsed.module.name, written, parsed.rel_path),
                                    PathMiss(written, parsed.rel_path, bases, matched))
        return Resolution(label=rel)

    def _file_node(self, base, suffixes):
        """The file node at base + one of the suffixes, else with base's extension dropped."""
        stem, ext = posixpath.splitext(base)
        for candidate in (base, stem) if ext else (base,):
            for suffix in suffixes:
                node_id = self.file_nodes.get(candidate + suffix)
                if node_id:
                    return node_id
        return None

    def _resolve_name(self, name, parsed, scope, use_aliases):
        steps = (
            lambda: use_aliases and self._via_aliases(name, self._file_aliases(parsed)),
            lambda: use_aliases and self._via_aliases(name, self.global_aliases),
            lambda: self._via_scope_chain(name, scope, use_aliases),
            lambda: self._pick(f"{imp}.{name}" for imp in parsed.result.imports),
            lambda: self._known(name) or self._via_prefix(name),
            lambda: self._via_suffix(name) if parsed.module.loose_names else None,
        )
        for step in steps:
            found = step()
            if found:
                return found
        return Resolution(label=name)

    @staticmethod
    def _file_aliases(parsed):
        return {name: (target, parsed) for name, target in parsed.result.aliases.items()}

    def _via_aliases(self, name, aliases):
        """aliases: name -> (Target, Parsed of the file that declared the alias)."""
        head, _, rest = name.partition(".")
        alias = aliases.get(name) or aliases.get(head)
        if not alias:
            return None
        target, origin = alias
        # Alias targets resolve without aliases, so names pointing at each other cannot loop.
        found = self.resolve(target, origin, use_aliases=False)
        if not found.id or name in aliases or not rest:
            return found
        member = self._member_of(found.id, rest)
        return Resolution(member) if member else found

    def _via_scope_chain(self, name, scope, use_aliases=True):
        """`scope.name` for each enclosing scope: a node, or a global alias with that full
        name (C++ namespaced functions register as `ns.func`)."""
        while scope:
            qualified = f"{scope}.{name}"
            found = self._known(qualified) or (use_aliases and self._global_alias(qualified))
            if found:
                return found
            scope = scope.rpartition(".")[0]
        return None

    def _global_alias(self, name):
        alias = self.global_aliases.get(name)
        if not alias:
            return None
        target, origin = alias
        found = self.resolve(target, origin, use_aliases=False)
        return found if found.id else None

    def _pick(self, candidates):
        found = sorted({c for c in candidates if c in self.graph.nodes})
        if not found:
            return None
        return Resolution(found[0], unsure=len(found) > 1)

    def _via_prefix(self, name):
        while "." in name:
            name = name.rpartition(".")[0]
            found = self._known(name)
            if found:
                return found
        return None

    def _via_suffix(self, name):
        return self._pick(i for i in self.graph.nodes if i.endswith(f".{name}"))


def build_graph(root, parsed_files, repo="", path_maps=None):
    """Make the graph: nodes from declarations, then edges from resolved refs.
    path_maps: module name -> PathMap (LanguageModule.read_path_map)."""
    graph = Graph(repo=repo)
    resolver = Resolver(root, graph, path_maps=dict(path_maps or {}))
    for parsed in parsed_files:
        add_declarations(graph, resolver, parsed)
        for name, target in parsed.result.global_aliases.items():
            resolver.global_aliases[name] = (target, parsed)
    resolver.link_pages(parsed_files)
    for parsed in parsed_files:
        apply_marks(graph, resolver, parsed)
        for ref in parsed.result.refs:
            add_ref(graph, resolver, parsed, ref)
    graph.fold_held_edges()
    graph.path_misses = list(resolver.path_misses.values())
    return graph


def count_lines(path):
    """Lines in a file (0 if it cannot be read)."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return 0
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def add_declarations(graph, resolver, parsed):
    file_lines = None  # read once, only if a declaration has no span of its own
    for decl in parsed.result.declarations:
        if decl.end_line:
            lines, span = decl.end_line - decl.line + 1, True
        else:
            if file_lines is None:
                file_lines = count_lines(Path(resolver.root) / parsed.rel_path)
            lines, span = file_lines, False
        graph.add_node(Node(
            id=decl.id, name=decl.name, kind=decl.kind,
            lang=decl.lang or parsed.module.name, file=parsed.rel_path, line=decl.line,
            flags=list(decl.flags), base=decl.base, namespace=decl.namespace,
            members=list(decl.members), imports=list(parsed.result.imports),
            lines=lines, span=span, doc=decl.doc,
        ))
        if decl.file_node:
            resolver.file_nodes.setdefault(parsed.rel_path, decl.id)
            resolver.file_parsed.setdefault(decl.id, parsed)


def apply_marks(graph, resolver, parsed):
    for mark in parsed.result.marks:
        found = resolver.resolve(mark.target, parsed)
        if found.id:
            node = graph.nodes[found.id]
            node.flags = node.flags + [f for f in mark.flags if f not in node.flags]


def add_ref(graph, resolver, parsed, ref):
    owner = graph.nodes.get(ref.from_id)
    if owner is None:
        return
    if ref.shared:
        found = resolver.resolve_shared(ref.to, parsed)
        if found is None or found.id == ref.from_id:
            return
    else:
        found = resolver.resolve(ref.to, parsed, ref.scope)
    if found.id == ref.from_id and ref.kind in STRUCT_KINDS:
        return  # a class cannot extend itself; other self references are internal calls
    location = Location(parsed.rel_path, ref.line)
    kind = ref.kind
    if kind == "generalization" and found.id and graph.nodes[found.id].kind == "interface":
        kind = "realization"
    edge = Edge(
        from_id=ref.from_id, to=found.id or found.label, kind=kind,
        detail=[ref.detail] if ref.detail else [],
        calls=[Call(ref.call, 1, [location], ref.from_member)] if ref.call else [],
        at=[location], unsure=found.unsure, auto=ref.auto,
    )
    if found.id:
        graph.add_edge(edge)
    else:
        owner.add_external(edge)
