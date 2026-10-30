"""HTTP routes and the clients that call them, as communications nodes (DESIGN 8).

A text pass over the source files, literals only (no data flow): server route strings, client
calls with a literal URL. Each owner class gets one `http.server` comm node (messages = its routes);
a client whose path matches a route (longest prefix) gets an edge to that node; an unmatched
client with a literal host gets an `http.client` node for the host; the rest is dropped."""

import re
from collections import defaultdict
from types import SimpleNamespace
from urllib.parse import urlsplit

from .filewalk import list_files
from .model import Call, Edge, Location, merge_locations
from .stack import CommUse, add_comm_node, comm_id

SERVER_TYPE, CLIENT_TYPE = "http.server", "http.client"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
METHODS = "get|post|put|delete|patch|head|options"
PATH = r"/[\w\-./~%:<>{}$]*"  # a route or URL path inside a string
BARE = r"/[\w\-/~%:<>{}]*"  # the same without a dot: loose patterns skip file names (`/index.js`)
NOT_CALLS = "startsWith|endsWith|includes|indexOf|test|match|replace|split|join|has|push|add|require"
Q = r"""(["'`])"""

# (pattern, group holding the path) per extension group. Server patterns:
PY_SERVER = [
    rf"""\b\w+\.path\s*==\s*["']({PATH})["']""",
    rf"""\bpath\s*==\s*["']({PATH})["']""",
    rf"""\bpath\.startswith\(\s*["']({PATH})["']""",
    rf"""@\w+(?:\.\w+)*\.(?:{METHODS}|route|api_route|websocket)\(\s*["']({PATH})["']""",
    rf"""\.add_(?:{METHODS}|route)\(\s*(?:["']\w+["']\s*,\s*)?["']({PATH})["']""",
    rf"""\bweb\.(?:{METHODS})\(\s*["']({PATH})["']""",
    rf"""["'](?:{METHODS})["']\s*,\s*["']({PATH})["']""",  # method then path
    rf"""["']({BARE})["']\s*,\s*lambda\b""",  # path then lambda
    rf"""["']({BARE})["']\s*:\s*(?:lambda\b|self\.\w|\w+\s*[,}}])""",  # dict entry
    rf"""\(\s*["']({BARE})["']\s*,\s*(?:self\.)?\w+(?:\.\w+)*\s*\)""",  # tuple of path and handler
]
PY_TABLE = re.compile(r"""\b\w*ROUTES?\w*\s*=\s*[(\[{]([^)\]}]*)[)\]}]""", re.I)
PY_TABLE_PATH = re.compile(rf"""["']({BARE})["']""")
JS_SERVER = [
    rf"""\b(?:app|router|server|api|\w*[Rr]outer|\w*[Aa]pp)\.(?:{METHODS}|all|use)\(\s*{Q}({PATH})\1""",
]
# Client patterns: group 2 is the URL (a template literal is cut at its first `${`).
JS_CLIENT = [
    rf"""\bfetch\(\s*(?:[\w.$]+\(\s*)?{Q}(.*?)\1""",
    rf"""\baxios(?:\.(?:{METHODS}|request))?\(\s*(?:[\w.$]+\(\s*)?{Q}(.*?)\1""",
    rf"""\$\.(?:get|post|getJSON|ajax)\(\s*{Q}(.*?)\1""",
    # any call whose first argument is a literal path (`api("/api/x")`, `CoderApps.request(...)`):
    # it only links when the path is a server route, so this guesses nothing else
    rf"""(?<![\w$.])(?:[\w$]+\.)*(?!(?:{NOT_CALLS})\()[\w$]+\(\s*{Q}(/[^"'`\n]*)\1""",
    rf"""\.open\(\s*["']\w+["']\s*,\s*{Q}(.*?)\1""",
]
PY_CLIENT = [
    rf"""\b(?:requests|httpx|\w*session|client|http)\.(?:{METHODS}|request)\(\s*(?:["']\w+["']\s*,\s*)?f?{Q}(.*?)\1""",
    rf"""\burlopen\(\s*f?{Q}(.*?)\1""",
]
GD_CLIENT = [rf"""\.request\(\s*{Q}(.*?)\1"""]

LANG_PATTERNS = {
    "py": (PY_SERVER, PY_CLIENT), "js": (JS_SERVER, JS_CLIENT), "gd": ([], GD_CLIENT),
}
EXTENSIONS = {"py": "py", "js": "js", "mjs": "js", "cjs": "js", "ts": "js", "tsx": "js",
              "jsx": "js", "gd": "gd"}
COMPILED = {lang: ([re.compile(p) for p in server], [re.compile(p) for p in client])
            for lang, (server, client) in LANG_PATTERNS.items()}
URL_START = re.compile(r"(https?://[^/?#\s]*)?(/[^?#\s]*)?")


def route_prefix(path):
    """A route with parameters (`/x/<id>`, `/x/{id}`, `/x/:id`) stops at the first one."""
    parts = []
    for part in path.split("/"):
        if re.search(r"[<{:$]", part):
            break
        parts.append(part)
    return "/".join(parts).rstrip("/")


def split_url(raw):
    """(host, path) of a literal URL or path (query and trailing slash dropped), or None when it
    is not one. A template literal arrives cut at its first `${`."""
    if not raw.startswith(("/", "http")):
        return None
    match = URL_START.fullmatch(re.split(r"[?#]|\$\{|\{", raw)[0])
    if not match or not (match[1] or match[2]):
        return None
    host = urlsplit(match[1]).netloc.rstrip(":") if match[1] else ""
    path = (match[2] or "").rstrip("/")
    return host, path


def match_route(path, routes):
    """The longest route that is `path` or a segment prefix of it, else None."""
    best = None
    for route in routes:
        if (path == route or path.startswith(route + "/")) and (best is None or len(route) > len(best)):
            best = route
    return best


def find_hits(text, patterns):
    """(group 1/2 text, line) for every match of the patterns."""
    for pattern in patterns:
        for found in pattern.finditer(text):
            yield found.group(found.lastindex), text.count("\n", 0, found.start()) + 1


def server_hits(text, lang, patterns):
    seen = set()
    for path, line in find_hits(text, patterns):
        seen.add((path, line))
    if lang == "py":
        for table in PY_TABLE.finditer(text):
            line = text.count("\n", 0, table.start()) + 1
            seen.update((p, line) for p in PY_TABLE_PATH.findall(table[1]))
    return seen


def read_hits(root, skip_dirs):
    """rel file -> (server [(route, line)], client [(raw url, line)])."""
    patterns = "*." + "|*.".join(EXTENSIONS)
    found = {}
    for rel in list_files(root, patterns.split("|"), skip_dirs):
        lang = EXTENSIONS[rel.rsplit(".", 1)[-1]]
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        server_patterns, client_patterns = COMPILED[lang]
        server = []
        if server_patterns and "/" in text:
            server = [(route_prefix(p), line) for p, line in server_hits(text, lang, server_patterns)]
            server = [(r, line) for r, line in server if r]
        client = list(find_hits(text, client_patterns)) if "/" in text else []
        if server or client:
            found[rel] = (sorted(set(server)), client)
    return found


class OwnerIndex:
    """Which node holds a line of a file: the smallest type whose span has it, else the file's
    first node (its module)."""

    def __init__(self, graph):
        self.by_file = defaultdict(list)
        for node in graph.nodes.values():
            if node.file and node.kind != "external" and not node.comm:
                self.by_file[node.file].append(node)

    def owner(self, rel, line):
        nodes = self.by_file.get(rel)
        if not nodes:
            return None
        inside = [n for n in nodes if n.span and n.line <= line < n.line + n.lines]
        if inside:
            return min(inside, key=lambda n: n.lines)
        return min(nodes, key=lambda n: (n.span, n.line))


def add_http_nodes(root, graph, skip_dirs):
    """Add the server and client comm nodes and the client -> server edges."""
    hits = read_hits(root, skip_dirs)
    if not hits:
        return
    owners = OwnerIndex(graph)
    routes = {}  # owner id -> {route: first line}
    for rel, (server, _) in sorted(hits.items()):
        for route, line in server:
            owner = owners.owner(rel, line)
            if owner:
                routes.setdefault(owner.id, {}).setdefault(route, Location(rel, line))
    server_of = {}  # route -> owner id (first owner wins)
    for owner_id, owned in routes.items():
        for route in owned:
            server_of.setdefault(route, owner_id)
    clients = defaultdict(lambda: defaultdict(list))  # owner id -> (host, path) -> [Location]
    for rel, (_, client) in sorted(hits.items()):
        for raw, line in client:
            url = split_url(raw)
            owner = owners.owner(rel, line) if url else None
            if owner:
                clients[owner.id][url].append(Location(rel, line))
    matched = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # owner -> server -> route -> at
    outside = defaultdict(lambda: defaultdict(list))  # owner -> host -> [(path, at)]
    for owner_id, urls in clients.items():
        for (host, path), at in urls.items():
            route = match_route(path, server_of) if host.lower().split(":")[0] in ("", *LOCAL_HOSTS) \
                else None
            if route and server_of[route] != owner_id:
                matched[owner_id][server_of[route]][route] += at
            elif host:
                outside[owner_id][host].append((path or "/", at))
    for owner_id, owned in routes.items():
        use = CommUse(messages=sorted(owned), at=sorted(owned.values()))
        add_comm_node(graph, graph.nodes[owner_id], SERVER_TYPE, None, use)
    for owner_id, servers in matched.items():
        for server_id, per_route in servers.items():
            link_client(graph, owner_id, comm_id(server_id, SERVER_TYPE), per_route)
    for owner_id, hosts in outside.items():
        for host, calls in hosts.items():
            at = sorted({loc for _, locs in calls for loc in locs})
            use = CommUse(messages=sorted({p for p, _ in calls}), at=at)
            use.calls.append(Call("request", len(at), at))
            add_comm_node(graph, graph.nodes[owner_id], CLIENT_TYPE, None, use, label=host)


def link_client(graph, owner_id, server_node_id, per_route):
    """The client's edge to a server node; each matched route is a call on it."""
    at = []
    calls = []
    for route, locs in sorted(per_route.items()):
        calls.append(Call(route, len(locs), sorted(locs)))
        at = merge_locations(at, sorted(locs))
    graph.add_edge(Edge(owner_id, server_node_id, "dependency", detail=["http"], at=at, calls=calls))
