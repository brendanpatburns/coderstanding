"""Communications nodes (DESIGN 8): sockets and network endpoints as their own nodes."""

from coderstanding.graph_json import graph_from_json, graph_to_json
from coderstanding.notes import Notes
from coderstanding.scan import scan_repo
from coderstanding.textout import view_text
from coderstanding.views import compute_view
from helpers import write_files
from coderstanding.stack import comm_id

GD = '''class_name Peer
extends Node
var _server := TCPServer.new()
var _ws: WebSocketPeer
var _peer: StreamPeerTCP = StreamPeerTCP.new()

@rpc("any_peer")
func hello(x): pass

func start():
    _server.listen(80)
    _ws.connect_to_url("ws://x")
    var http := HTTPRequest.new()
    http.request("x")
    var c := _peer as StreamPeerTCP
    var local_only: UDPServer = null
    if c is PacketPeerUDP:
        pass
    var s: HTTPClient = null
    var m = s.METHOD_GET
'''
CS = '''using System.Net.Sockets;
public class Net {
    private TcpListener _listener;
    public void Go() {
        _listener.Start();
        var c = new HttpClient();
        c.GetAsync("x");
        var t = x as TcpClient;
        if (x is UdpClient) {}
    }
}
'''
PY = '''import socket
import websockets
import asyncio
from http.server import HTTPServer

class Server:
    def __init__(self):
        self.http = HTTPServer(("", 80), None)

    async def run(self):
        s = await websockets.serve(self.h, "localhost", 8)
        async with websockets.connect("ws://x") as ws:
            pass
        sock = socket.socket()
        return isinstance(sock, socket.socket)
'''
JS = '''import net from "net";
import http from "http";
class Hub {
  constructor() { this.sock = new WebSocket("ws://y"); this.sock.send("a"); this.sock.on("message", () => {}); }
}
net.createServer(() => {});
'''


def scan(tmp_path, files):
    write_files(tmp_path, files)
    return scan_repo(tmp_path, use_cache=False, workers=1)


def comm_nodes(graph):
    return {i: n for i, n in graph.nodes.items() if n.comm}


def test_godot_sockets_by_field_and_by_code(tmp_path):
    graph = scan(tmp_path, {"peer.gd": GD})
    nodes = comm_nodes(graph)
    assert set(nodes) == {"comm:Peer#_server", "comm:Peer#_ws", "comm:Peer#_peer",
                          "comm:Peer@HTTPRequest"}  # is / as / bare locals / constants: none
    server = nodes["comm:Peer#_server"]
    assert (server.kind, server.namespace, server.file) == ("external", "comm", "")
    assert server.name == "_server · TCPServer (listens)"
    assert nodes["comm:Peer#_ws"].comm["role"] == "connects"
    assert nodes["comm:Peer#_peer"].comm["role"] == ""
    assert nodes["comm:Peer@HTTPRequest"].name == "HTTPRequest (connects)"
    held = graph.edges[("Peer", "comm:Peer#_server", "composition")]
    assert held.detail == ["field"] and [c.member for c in held.calls] == ["listen"]
    assert "create" in graph.edges[("Peer", "comm:Peer@HTTPRequest", "dependency")].detail


def test_type_checks_alone_make_no_node(tmp_path):
    graph = scan(tmp_path, {"a.gd": "extends Node\nfunc f(x):\n\tvar c := x as TCPServer\n"
                                    "\tif x is WebSocketPeer:\n\t\tpass\n"})
    assert not comm_nodes(graph)


def test_csharp_sockets(tmp_path):
    nodes = comm_nodes(scan(tmp_path, {"Net.cs": CS}))
    assert set(nodes) == {"comm:Net#_listener", "comm:Net@HttpClient"}
    assert nodes["comm:Net#_listener"].comm["role"] == "listens"


def test_python_sockets(tmp_path):
    nodes = comm_nodes(scan(tmp_path, {"srv.py": PY}))
    assert set(nodes) == {"comm:srv.Server#http", comm_id("srv.Server", "websockets.serve"),
                          comm_id("srv.Server", "websockets.connect"), comm_id("srv.Server", "socket.socket")}
    assert nodes[comm_id("srv.Server", "websockets.serve")].comm["role"] == "listens"
    assert nodes[comm_id("srv.Server", "websockets.connect")].comm["role"] == "connects"


def test_javascript_sockets_and_events(tmp_path):
    nodes = comm_nodes(scan(tmp_path, {"hub.js": JS}))
    assert set(nodes) == {"comm:hub.js.Hub#sock", comm_id("hub.js", "net.createServer")}
    assert nodes["comm:hub.js.Hub#sock"].comm["messages"] == ["message"]
    assert nodes[comm_id("hub.js", "net.createServer")].comm["role"] == "listens"


def test_ids_are_stable_across_rescans_and_edits(tmp_path):
    first = scan(tmp_path, {"peer.gd": GD})
    (tmp_path / "peer.gd").write_text("# moved down\n\n\n" + GD)
    second = scan_repo(tmp_path, use_cache=False, workers=1)
    assert sorted(comm_nodes(first)) == sorted(comm_nodes(second))


def test_rpc_flag_and_json_round_trip(tmp_path):
    graph = scan(tmp_path, {"peer.gd": GD})
    assert [m.name for m in graph.nodes["Peer"].members if m.rpc] == ["hello"]
    data = graph_to_json(graph)
    assert graph_to_json(graph_from_json(data)) == data
    plain = [m for n in data["nodes"] if n["id"] == "Peer" for m in n["members"] if m["name"] == "start"]
    assert "rpc" not in plain[0]


def test_view_toggle_hides_only_comm_nodes(tmp_path):
    graph = scan(tmp_path, {"peer.gd": GD})
    ids = lambda **show: {n["id"] for n in compute_view(graph, Notes(), show=show)["nodes"]}
    assert "comm:Peer#_server" in ids()
    assert "comm:Peer#_server" not in ids(comm=False)
    assert "comm:Peer#_server" in ids(external=False)  # a separate switch from Outside code


def test_text_tags_and_lists_role_and_rpcs(tmp_path):
    graph = scan(tmp_path, {"peer.gd": GD})
    text = view_text("app", graph, Notes(), level=3)
    assert "_server · TCPServer (listens) [comm]" in text
    assert "comm _server · TCPServer (listens) [comm]: owner Peer, listens" in text
    assert "rpc Peer: hello" in text
    off = view_text("app", graph, Notes(), level=3, show={"comm": False})
    assert "[comm]" not in off and "--no-comm" in off
