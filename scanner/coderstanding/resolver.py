"""Shared name resolution (docs/DESIGN.md 4.1): declarations + raw refs -> Graph.

Order for a name: file aliases, global aliases, scope chain (innermost first; nodes, then
global aliases with the scoped name), imports,
global id, longest existing dotted prefix, then (loose modules only) any id ending in
".Name". Several matches at one step -> first match, unsure. No match -> external ref.
"""

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

    def resolve(self, target, parsed, scope="", use_aliases=True):
        resolution = self._resolve_base(target, parsed, scope or target.scope, use_aliases)
        if target.member and resolution.id:
            return self._with_member(resolution, target.member)
        if target.member:
            return Resolution(label=target.text)
        return resolution

    def _resolve_base(self, target, parsed, scope, use_aliases):
        if target.id:
            return self._known(target.id) or Resolution(label=target.id)
        if target.path:
            return self._resolve_path(target.path, parsed)
        return self._resolve_name(target.name, parsed, scope, use_aliases)

    def _known(self, node_id):
        return Resolution(node_id) if node_id in self.graph.nodes else None

    def _with_member(self, resolution, member):
        found = self._known(child_id(resolution.id, member))
        return Resolution(found.id if found else resolution.id, unsure=resolution.unsure)

    def _resolve_path(self, written, parsed):
        rel = paths.normalize_ref_path(written, parsed.rel_path, self.root)
        for suffix in parsed.module.path_suffixes:
            node_id = self.file_nodes.get(rel + suffix)
            if node_id:
                return Resolution(node_id)
        return Resolution(label=rel)

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
        return self._known(child_id(found.id, rest)) or found

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


def build_graph(root, parsed_files, repo=""):
    """Make the graph: nodes from declarations, then edges from resolved refs."""
    graph = Graph(repo=repo)
    resolver = Resolver(root, graph)
    for parsed in parsed_files:
        add_declarations(graph, resolver, parsed)
        for name, target in parsed.result.global_aliases.items():
            resolver.global_aliases[name] = (target, parsed)
    for parsed in parsed_files:
        apply_marks(graph, resolver, parsed)
        for ref in parsed.result.refs:
            add_ref(graph, resolver, parsed, ref)
    graph.fold_held_edges()
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
