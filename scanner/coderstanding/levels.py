"""Rough first level guess (docs/DESIGN.md 4.5 steps 1-2). Claude curates levels later."""

from collections import deque

from .checks import checked_ids, god_threshold, neighbours
from .model import APP_START_FLAGS, HOLDING_KINDS, TOOL_FLAGS
from .testcode import is_test

TOP_FLAGS = ("singleton",) + APP_START_FLAGS
TOOL_DISTANCE = 1  # with an app start, tools start one step down: level 2, their parts below
DEFAULT_MAX_LEVEL = 5
AUTO = "auto"  # hub threshold: same as the god-class threshold
OFF = 0  # hub threshold: no hubs


def parse_hub_threshold(value):
    """`auto`, `off`/`0`, or a positive integer (text or number) -> AUTO or an int."""
    text = str(value).strip().lower()
    if text == AUTO:
        return AUTO
    if text == "off":
        return OFF
    try:
        number = int(text)
    except ValueError:
        raise ValueError(f"hub_threshold must be a number, auto or off, got {value}") from None
    if number < 0:
        raise ValueError(f"hub_threshold must not be negative, got {value}")
    return number


def find_hubs(graph, hub_threshold):
    """(threshold, {hub id: neighbour count} busiest first): classes whose distinct-neighbour count (the
    god-class count, checks.neighbours) is at least the threshold."""
    if hub_threshold == OFF:
        return OFF, {}
    links = neighbours(graph, checked_ids(graph))
    degrees = {i: len(n) for i, n in links.items()}
    threshold = (god_threshold(list(degrees.values())) if hub_threshold == AUTO
                 else hub_threshold)
    hubs = sorted((i for i, d in degrees.items() if d >= threshold),
                  key=lambda i: (-degrees[i], i))
    return threshold, {i: degrees[i] for i in hubs}


def holding_children(graph):
    children = {node_id: [] for node_id in graph.nodes}
    for edge in graph.edges.values():
        if edge.kind in HOLDING_KINDS and edge.from_id in children and edge.to != edge.from_id:
            children[edge.from_id].append(edge.to)
    return children


def has_flag(node, flags):
    return bool(set(node.flags) & set(flags))


def is_tool(node):
    """A standalone runnable script (`script-main`) that is not also part of the app start."""
    return has_flag(node, TOOL_FLAGS) and not has_flag(node, TOP_FLAGS)


def is_app_start(node):
    """Autoload, singleton, Godot main, program main; a bare `entry` flag counts too."""
    return has_flag(node, TOP_FLAGS) or ("entry" in node.flags and not has_flag(node, TOOL_FLAGS))


def top_nodes(graph, children):
    """Level-1 nodes: app starts, else standalone scripts (tools), else holders nothing
    else holds."""
    for found in (is_app_start, is_tool):
        flagged = [i for i, n in graph.nodes.items() if found(n)]
        if flagged:
            return flagged
    held = {to for tos in children.values() for to in tos}
    return [i for i, tos in children.items()
            if tos and i not in held and not is_test(graph.nodes[i])]


def guess_levels(graph, max_level=DEFAULT_MAX_LEVEL, hub_threshold=AUTO):
    """Set node.level: 1 + shortest holding distance from a level-1 node, capped.
    Tools (standalone scripts) are level 1 only when nothing starts the app; else level 2+.
    Hubs (too many connections, see find_hubs) are level 1 too; their parts follow from them.
    Sets graph.settings and graph.hubs."""
    children = holding_children(graph)
    threshold, hubs = find_hubs(graph, hub_threshold)
    graph.settings = {"hub_threshold": hub_threshold}
    graph.hubs = {"threshold": threshold, "ids": list(hubs)}
    for node_id, degree in hubs.items():
        node = graph.nodes[node_id]
        node.hub_degree = degree
        if "hub" not in node.flags:
            node.flags.append("hub")
    distance = {node_id: 0 for node_id in top_nodes(graph, children)}
    if any(is_app_start(n) for n in graph.nodes.values()):
        for node_id, node in graph.nodes.items():
            if is_tool(node):
                distance.setdefault(node_id, TOOL_DISTANCE)
    distance.update({node_id: 0 for node_id in hubs})
    queue = deque(distance)
    while queue:
        node_id = queue.popleft()
        for child in children[node_id]:
            if child not in distance:
                distance[child] = distance[node_id] + 1
                queue.append(child)
    for node_id, node in graph.nodes.items():
        node.level = min(distance.get(node_id, max_level) + 1, max_level)
