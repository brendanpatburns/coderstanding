from pathlib import Path

from coderstanding.model import Call, Edge, Graph, Location, Member, Node
from coderstanding.scan import scan_repo
from fake_lang import FAKE


def write_files(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def scan_fake(root, files):
    write_files(root, files)
    return scan_repo(root, modules=[FAKE])


def edge(graph, from_id, to, kind):
    """The graph's edge from_id -> to of this kind; fails with the edges it does have."""
    key = (from_id, to, kind)
    assert key in graph.edges, f"missing {kind} {from_id} -> {to}; have {sorted(graph.edges)}"
    return graph.edges[key]


def calls(edge_):
    return {c.member: c.count for c in edge_.calls}


# Function fan-out fixture (DESIGN 12c): Starter (level 2) folds into Game at level 1.
def fan_node(node_id, level, functions=(), fields=()):
    members = [Member(f, "method", line=i + 2) for i, f in enumerate(functions)]
    members += [Member(f, "field", "VFX") for f in fields]
    return Node(id=node_id, name=node_id, kind="class", lang="x", file=f"{node_id}.x",
                level=level, members=members)


def fan_call(from_member, member, count=1, line=1):
    return Call(member, count, [Location("x.x", line)], from_member)


def fan_graph():
    graph = Graph(repo="t")
    for n in (fan_node("Player", 1, ["_physics_process", "take_hit", "die", "play_step"], ["vfx"]),
              fan_node("VFX", 1, ["push_effect", "clear_effects", "spawn_sparks"]),
              fan_node("Audio", 1, ["play_sfx"]),
              fan_node("Game", 1, ["_ready"]), fan_node("Starter", 2, ["start"])):
        graph.add_node(n)
    for fan_edge in (
            Edge("Player", "VFX", "composition", ["field", "call"],
                 [fan_call("take_hit", "push_effect", 2, 10),
                  fan_call("die", "clear_effects", 1, 20)]),
            Edge("Player", "Audio", "association", ["call"],
                 [fan_call("play_step", "play_sfx", 3), fan_call(None, "volume")]),
            Edge("Player", "Player", "association", ["call"],
                 [fan_call("_physics_process", "play_step")]),
            Edge("Game", "Starter", "composition", ["field", "call"],
                 [fan_call("_ready", "start")]),
            Edge("Starter", "VFX", "association", ["call"], [fan_call("start", "spawn_sparks")]),
            Edge("Audio", "VFX", "association", ["call"], [fan_call("<init>", "push_effect")])):
        graph.edges[fan_edge.key] = fan_edge  # incl. a self-edge, which the resolver never makes
    return graph
