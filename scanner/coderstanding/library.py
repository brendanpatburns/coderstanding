"""Library code: nodes the `library` setting marks as outside code (statically linked libraries
inside the repo's own sources). Applied when the map is loaded, not at scan time, because the
tag and domain rules read the notes. A marked node becomes an `external` node (hidden by
`--no-external`, listed by `--all`, no warnings, no hub or level of its own) and keeps its
notes and domain; `apply_library` undoes the marks when the rules change.
"""

from fnmatch import fnmatchcase

from .checks import glob_regex

LIBRARY_FLAG = "library"
RULE_KINDS = ("tag", "domain", "name")  # `kind:value` rules; any other text is a path glob


def parse_library_rule(text):
    """A library rule as stored (its text): `tag:library`, `domain:Graphics Library`, `name:AIL_*`,
    or a repo-relative path glob (`out/pseudo/0x5*.c`, the folder-rule globs). ValueError if empty."""
    text = str(text).strip()
    kind, _, value = text.partition(":")
    if not text or (kind in RULE_KINDS and not value.strip()):
        raise ValueError("library: expected tag:NAME, domain:NAME, name:PATTERN or a path glob")
    return f"{kind}:{value.strip()}" if kind in RULE_KINDS else text


def rule_matches(rule, node, notes):
    kind, _, value = rule.partition(":")
    if kind == "tag":
        return value in notes.tags_of(node.id)
    if kind == "domain":
        return notes.domain_of(node) == value
    if kind == "name":
        return fnmatchcase(node.name, value)
    return glob_regex(rule).match(node.file or "") is not None


def library_ids(graph, notes, rules):
    """Ids of the own-code nodes any rule matches."""
    return {node_id for node_id, node in graph.nodes.items()
            if (node.kind != "external" or node_id in graph.library_kinds)
            and any(rule_matches(rule, node, notes) for rule in rules)}


def apply_library(graph, notes, rules):
    """Mark the nodes `rules` match as outside code and unmark the ones no rule matches now.
    True when the marks changed, so the caller redoes levels and warnings for own code."""
    wanted = library_ids(graph, notes, rules)
    if wanted == set(graph.library_kinds):
        return False
    for node_id in set(graph.library_kinds) - wanted:
        node = graph.nodes[node_id]
        node.kind = graph.library_kinds.pop(node_id)
        node.flags.remove(LIBRARY_FLAG)
    for node_id in wanted - set(graph.library_kinds):
        node = graph.nodes[node_id]
        graph.library_kinds[node_id] = node.kind
        node.kind = "external"
        node.flags.append(LIBRARY_FLAG)
    return True
