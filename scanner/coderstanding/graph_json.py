"""Read and write graph.json (version 2, see docs/DESIGN.md section 10)."""

import json
from pathlib import Path

from .cache import SCANNER_VERSION
from .model import Call, Edge, Graph, Location, Member, Node

VERSION = 2  # 2: node size (lines, span), warnings, test flag


def _edge_body(edge):
    return {
        "kind": edge.kind,
        "detail": edge.detail,
        "calls": [{"member": c.member, "from_member": c.from_member, "count": c.count,
                   "at": [a.to_json() for a in c.at]} for c in edge.calls],
        "at": [a.to_json() for a in edge.at],
        "unsure": edge.unsure,
        "auto": edge.auto,
    }


def edge_to_json(edge):
    return {"from": edge.from_id, "to": edge.to, **_edge_body(edge)}


def external_to_json(edge):
    return {"to": edge.to, **_edge_body(edge)}


def member_to_json(member):
    """A member as a dict; `rpc` only when set, so graphs without RPCs stay as they were."""
    data = vars(member)
    return data if member.rpc else {k: v for k, v in data.items() if k != "rpc"}


def node_to_json(node):
    data = {"id": node.id, "name": node.name, "kind": node.kind, "lang": node.lang,
            "file": node.file, "line": node.line, "flags": node.flags,
            "base": node.base, "level": node.level, "lines": node.lines}
    if node.span:
        data["span"] = True
    if node.doc:
        data["doc"] = node.doc
    if node.hub_degree:
        data["hub_degree"] = node.hub_degree
    if node.warnings:
        data["warnings"] = node.warnings
    if node.namespace:
        data["namespace"] = node.namespace
    if node.members:
        data["members"] = [member_to_json(m) for m in node.members]
    if node.comm:
        data["comm"] = node.comm
    if node.external_refs:
        data["external_refs"] = [external_to_json(e)
                                 for _, e in sorted(node.external_refs.items())]
    return data


def graph_to_json(graph):
    return {
        "version": VERSION,
        "scannerVersion": SCANNER_VERSION,
        "repo": graph.repo,
        "scannedAt": graph.scanned_at,
        "nodes": [node_to_json(n) for _, n in sorted(graph.nodes.items())],
        "edges": [edge_to_json(e) for _, e in sorted(graph.edges.items())],
        "stack": graph.stack,
        "settings": graph.settings,
        "hubs": graph.hubs,
        "fingerprints": graph.fingerprints,
        "diagnostics": graph.diagnostics,
    }


def edge_from_json(data, from_id=None):
    return Edge(
        from_id=from_id or data["from"], to=data["to"], kind=data["kind"],
        detail=list(data.get("detail", [])),
        calls=[Call(c["member"], c["count"], [Location.from_json(a) for a in c["at"]],
                    c.get("from_member"))
               for c in data.get("calls", [])],
        at=[Location.from_json(a) for a in data.get("at", [])],
        unsure=data.get("unsure", False), auto=data.get("auto", ""),
    )


def node_from_json(data):
    node = Node(
        id=data["id"], name=data["name"], kind=data["kind"], lang=data["lang"],
        file=data["file"], line=data.get("line", 1), flags=list(data.get("flags", [])),
        base=data.get("base", ""), namespace=data.get("namespace", ""),
        doc=data.get("doc", ""),
        level=data.get("level", 0), lines=data.get("lines", 0), span=data.get("span", False),
        warnings=list(data.get("warnings", [])), hub_degree=data.get("hub_degree", 0),
        members=[Member(**m) for m in data.get("members", [])], comm=data.get("comm", {}),
    )
    for ext in data.get("external_refs", []):
        node.add_external(edge_from_json(ext, from_id=node.id))
    return node


def graph_from_json(data):
    if data.get("version") != VERSION:
        raise ValueError(f"unsupported graph.json version: {data.get('version')}")
    # Any scanner version loads (only an unknown format refuses); callers note an old one.
    graph = Graph(repo=data.get("repo", ""), scanned_at=data.get("scannedAt", ""),
                  stack=data.get("stack", {}), settings=data.get("settings", {}),
                  hubs=data.get("hubs", {}), scanner_version=data.get("scannerVersion") or 0, fingerprints=data.get("fingerprints", {}),
                  diagnostics=data.get("diagnostics", {}))
    for node in data["nodes"]:
        graph.add_node(node_from_json(node))
    for edge in data["edges"]:
        graph.add_edge(edge_from_json(edge))
    return graph


def write_graph(graph, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph_to_json(graph), indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def graph_summary(path):
    """{nodes, edges, scannedAt, scannerVersion} of a graph.json without building the model, so
    a map from any scanner version can be described (nodes: not counting outside code)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"nodes": sum(node.get("kind") != "external" for node in data.get("nodes", [])),
            "edges": len(data.get("edges", [])), "scannedAt": data.get("scannedAt"),
            "scannerVersion": data.get("scannerVersion")}


def read_graph(path):
    return graph_from_json(json.loads(Path(path).read_text(encoding="utf-8")))
