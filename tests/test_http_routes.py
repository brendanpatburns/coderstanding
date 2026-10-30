"""HTTP clients linked to the server routes they call (DESIGN 8)."""

from coderstanding.http_routes import match_route, route_prefix, split_url
from coderstanding.scan import scan_repo
from helpers import write_files
from coderstanding.stack import comm_id

SERVER_ID = comm_id("srv.Handler", "http.server")
HANDLER = '''from http.server import BaseHTTPRequestHandler

ROUTES = ("/api/apps", "/api/rescan")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path
        if path == "/api/meta":
            return 1
        if path.startswith("/api/node/"):
            return 2
        if path == "/api/tours":
            return 3
        self.route_write("PUT", "/api/layout", lambda body: 1)
'''
FLASK = '''from flask import Flask
app = Flask(__name__)

@app.get("/items")
def items(): pass

@app.route("/items/<int:id>", methods=["POST"])
def one(id): pass

@router.post("/fast/{id}")
def fast(id): pass
'''
AIOHTTP = '''from aiohttp import web
class Web:
    def setup(self, app):
        app.router.add_get("/ping", self.ping)
        app.router.add_route("POST", "/send", self.send)
        web.get("/status", self.status)
'''
EXPRESS = '''const app = require("express")();
app.get("/users/:id", (req, res) => {});
router.post("/login", handler);
'''


def comm(graph, node_id):
    return graph.nodes[node_id].comm


def scan(tmp_path, files):
    write_files(tmp_path, files)
    return scan_repo(tmp_path, use_cache=False, workers=1)


def call_names(graph, source, target):
    return sorted(c.member for c in graph.edges[(source, target, "dependency")].calls)


def test_python_server_patterns(tmp_path):
    graph = scan(tmp_path, {"srv.py": HANDLER})
    info = comm(graph, SERVER_ID)
    assert info["role"] == "listens" and info["type"] == "http.server"
    assert info["messages"] == ["/api/layout", "/api/meta", "/api/node", "/api/tours"]  # ==, startswith, pair
    assert comm(graph, comm_id("srv", "http.server"))["messages"] == ["/api/apps", "/api/rescan"]  # a routes table


def test_flask_fastapi_aiohttp_and_express_servers(tmp_path):
    graph = scan(tmp_path, {"flask_app.py": FLASK, "w.py": AIOHTTP, "s.js": EXPRESS})
    servers = {n.comm["owner"]: n.comm["messages"] for n in graph.nodes.values()
               if n.comm.get("type") == "http.server"}
    assert servers["flask_app"] == ["/fast", "/items"]  # parameters cut the route
    assert servers["w.Web"] == ["/ping", "/send", "/status"]
    assert servers["s.js"] == ["/login", "/users"]


def test_client_matches_longest_prefix_ignoring_query_and_slash(tmp_path):
    client = '''const a = fetch("/api/tours/");
const b = fetch(`/api/node/${id}?x=1`);
const c = fetch("/api/nothing");
async function d() { return fetch(url("/api/meta?full=1")); }
'''
    graph = scan(tmp_path, {"srv.py": HANDLER, "viewer/c.js": client})
    assert call_names(graph, "viewer/c.js", SERVER_ID) == ["/api/meta", "/api/node", "/api/tours"]
    assert not [n for n in graph.nodes.values() if n.comm.get("type") == "http.client"]  # unmatched: dropped


def test_wrapper_call_with_literal_path_links(tmp_path):
    graph = scan(tmp_path, {"srv.py": HANDLER, "v.js": 'await api("/api/layout", { method: "PUT" });\n'
                                                   'if (p.startsWith("/api/meta")) {}\n'})
    assert call_names(graph, "v.js", SERVER_ID) == ["/api/layout"]


def test_unmatched_literal_host_makes_an_outside_node(tmp_path):
    files = {"srv.py": HANDLER,
             "get.py": 'import requests\ndef f():\n    requests.get("https://api.example.com/v1/x?q=1")\n'
                       '    requests.post(f"http://localhost:80/api/meta")\n',
             "v.js": 'axios.get("https://cdn.example.org/lib.js");\n'}
    graph = scan(tmp_path, files)
    outside = {n.comm["field"]: n.comm for n in graph.nodes.values() if n.comm.get("type") == "http.client"}
    assert set(outside) == {"api.example.com", "cdn.example.org"}
    assert outside["api.example.com"]["messages"] == ["/v1/x"]
    assert outside["api.example.com"]["role"] == "connects"
    assert call_names(graph, "get", SERVER_ID) == ["/api/meta"]  # a local host links to the server


def test_gdscript_literal_url_and_dynamic_urls_dropped(tmp_path):
    gd = 'extends Node\nfunc go():\n\t$Http.request("https://games.example.com/list")\n\t$Http.request(url)\n'
    graph = scan(tmp_path, {"a.gd": gd, "v.js": "fetch(url);\nfetch(`${base}/api/meta`);\n"})
    assert [n.comm["field"] for n in graph.nodes.values() if n.comm.get("type") == "http.client"] \
        == ["games.example.com"]


def test_helpers():
    assert route_prefix("/a/<id>/b") == "/a" and route_prefix("/a/") == "/a"
    assert split_url("/a/b/?x=1") == ("", "/a/b")
    assert split_url("http://h:8/p/${x}") == ("h:8", "/p")
    assert split_url("name") is None
    assert match_route("/api/node/5", ["/api", "/api/node", "/api/no"]) == "/api/node"
