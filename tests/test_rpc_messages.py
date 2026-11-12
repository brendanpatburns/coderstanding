"""Network messages (DESIGN 8): one message box per (handling class, sender class), with the
sender -> message -> handler lines."""

from coderstanding.notes import Notes
from coderstanding.rpc_messages import (ANY_DIRECTION, CLIENT_TO_SERVER, SERVER_TO_CLIENT,
                                        message_directions, message_group_id, rpc_direction)
from coderstanding.scan import scan_repo
from coderstanding.textout import node_detail, view_text
from coderstanding.views import ViewContext, compute_view, find
from helpers import write_files

NET = '''class_name Net
extends Node

## Tells the server where we are (_c_position).
func send_position(pos):
\t_c_position.rpc_id(1, pos)

func say(text):
\trpc_id(1, "_c_chat", text)

func shout():
\t_s_shout.rpc()

func _send(peer, method, args):
\tcallv("rpc_id", [peer, method] + args)

@rpc("any_peer", "call_remote", "reliable")
func _c_position(pos):
\t_moved(pos)

@rpc("any_peer")
func _c_chat(text):
\tpass

@rpc
func _s_notice(text):
\tpass

@rpc("authority", "call_local")
func _s_shout():
\tpass

@rpc("any_peer", "call_local")
func ping():
\tpass

func _moved(pos):
\t_c_position(pos)  # a plain local call: not a send
'''
CHAT = '''class_name Chat
extends Node
var net: Net

func warn(peer):
\tnet._send(peer, "_s_notice", ["slow down"])
\tvar text = "_s_notice is sent here"  # prose in a string: not a send

func everyone():
\tnet.ping.rpc()
'''


def scan(tmp_path, files=None):
    write_files(tmp_path, files or {"net.gd": NET, "chat.gd": CHAT})
    return scan_repo(tmp_path, use_cache=False, workers=1)


GROUP = message_group_id("Net", "Chat")  # the box of what Chat sends to Net
OWN = message_group_id("Net", "Net")  # what Net sends to itself
UNSENT = message_group_id("Net")  # what nobody sends


def test_direction_comes_from_the_mode_not_the_name():
    assert rpc_direction("any_peer call_remote reliable") == CLIENT_TO_SERVER
    assert rpc_direction("authority") == SERVER_TO_CLIENT
    assert rpc_direction("call_local") == SERVER_TO_CLIENT  # no mode word: Godot's default authority
    assert rpc_direction("AnyPeer CallLocal") == CLIENT_TO_SERVER  # C#-style words
    assert rpc_direction("any_peer", broadcast=True) == ANY_DIRECTION


def test_reader_keeps_the_rpc_mode(tmp_path):
    graph = scan(tmp_path)
    modes = {m.name: m.rpc for m in graph.nodes["Net"].members if m.rpc}
    assert modes == {"_c_position": "any_peer call_remote reliable", "_c_chat": "any_peer",
                     "_s_notice": "authority", "_s_shout": "authority call_local",
                     "ping": "any_peer call_local"}


def test_one_message_box_per_sender_with_directions(tmp_path):
    graph = scan(tmp_path)
    own, chat = graph.nodes[OWN], graph.nodes[GROUP]
    assert (own.kind, own.namespace, own.name) == ("external", "comm", "Net messages ← Net")
    assert chat.name == "Net messages ← Chat"
    assert [m.name for m in own.members] == ["_c_position", "_c_chat", "_s_shout"]
    assert [m.name for m in chat.members] == ["_s_notice", "ping"]
    assert own.comm["directions"] == {
        "_c_position": CLIENT_TO_SERVER, "_c_chat": CLIENT_TO_SERVER, "_s_shout": SERVER_TO_CLIENT}
    assert chat.comm["directions"] == {"_s_notice": SERVER_TO_CLIENT, "ping": ANY_DIRECTION}  # ping: broadcast by Chat
    assert UNSENT not in graph.nodes  # every message has a sender


def test_message_nobody_sends_goes_to_the_not_sent_box(tmp_path):
    graph = scan(tmp_path, {"net.gd": NET})
    assert [m.name for m in graph.nodes[UNSENT].members] == ["_s_notice", "ping"]
    assert graph.nodes[UNSENT].name == "Net messages (not sent)"


def test_message_sent_from_two_classes_is_a_row_in_both_boxes(tmp_path):
    other = 'class_name Other\nextends Node\nfunc go():\n\tNet._send(1, "_s_notice", [])\n'
    graph = scan(tmp_path, {"net.gd": NET, "chat.gd": CHAT, "other.gd": other})
    assert "_s_notice" in [m.name for m in graph.nodes[GROUP].members]
    assert "_s_notice" in [m.name for m in graph.nodes[message_group_id("Net", "Other")].members]


def test_sender_to_message_to_handler_lines(tmp_path):
    graph = scan(tmp_path)
    sent = graph.edges[("Net", OWN, "dependency")]
    assert sent.detail == ["rpc"]
    assert {(c.from_member, c.member) for c in sent.calls} == {
        ("send_position", "_c_position"), ("say", "_c_chat"), ("shout", "_s_shout")}
    chat = graph.edges[("Chat", GROUP, "dependency")]
    assert {(c.from_member, c.member, c.count) for c in chat.calls} == {
        ("warn", "_s_notice", 1), ("everyone", "ping", 1)}  # the prose string is not a send
    handled = graph.edges[(GROUP, "Net", "dependency")]
    assert {(c.from_member, c.member) for c in handled.calls} == {("_s_notice", "_s_notice"), ("ping", "ping")}


def test_same_name_in_two_handlers_goes_to_the_one_the_sender_uses(tmp_path):
    other = 'class_name Addons\nextends Node\n@rpc\nfunc _s_notice(text):\n\tpass\n'
    graph = scan(tmp_path, {"net.gd": NET, "chat.gd": CHAT, "addons.gd": other})
    calls = {c.member for c in graph.edges[("Chat", GROUP, "dependency")].calls}
    assert "_s_notice" in calls
    assert ("Chat", message_group_id("Addons", "Chat"), "dependency") not in graph.edges


def test_message_node_never_holds_a_hidden_class(tmp_path):
    graph = scan(tmp_path, {"net.gd": NET})  # its only incoming line is from its message node
    assert ViewContext(graph, Notes()).owner("Net") is None
    assert UNSENT in {n["id"] for n in compute_view(graph, Notes(), level=1)["nodes"]}  # always shown


def test_message_node_opens_into_its_messages(tmp_path):
    graph = scan(tmp_path)
    view = compute_view(graph, Notes(), level=3, expand=[GROUP, OWN])
    rows = {f["name"]: f.get("direction") for f in view["expanded"][GROUP]["functions"]}
    assert rows == {"_s_notice": SERVER_TO_CLIENT, "ping": ANY_DIRECTION}
    own_rows = {f["name"]: f.get("direction") for f in view["expanded"][OWN]["functions"]}
    assert own_rows["_c_position"] == CLIENT_TO_SERVER
    lines = {(l["from"], l["from_fn"], l["to"], l["to_fn"]) for l in view["fanout"]}
    assert ("Chat", None, GROUP, "_s_notice") in lines
    assert (GROUP, "_s_notice", "Net", None) in lines
    star = compute_view(graph, Notes(), level=3, expand=["*"])
    assert GROUP in star["expanded"]


def test_text_lists_sends_and_handles(tmp_path):
    graph = scan(tmp_path)
    text = view_text("app", graph, Notes(), level=3)
    assert "comm Net messages ← Net [comm]: handler Net, messages (3): _c_chat (client→server), _c_position (client→server)" in text
    view = compute_view(graph, Notes(), level=3)
    chat = next(n for n in view["nodes"] if n["id"] == "Chat")
    assert chat["messages"]["sends"] == [
        {"name": "_s_notice", "direction": SERVER_TO_CLIENT, "to": "Net"},
        {"name": "ping", "direction": ANY_DIRECTION, "to": "Net"}]
    assert "sends: → _s_notice (server→client), → ping (any)" in node_detail(chat)
    net = next(n for n in view["nodes"] if n["id"] == "Net")
    assert any(line.startswith("handles: ← _c_position (client→server)") for line in node_detail(net))


def test_find_marks_the_message(tmp_path):
    graph = scan(tmp_path)
    hits = find(graph, Notes(), ["position"])
    hit = next(h for h in hits if h["unit"] == ("Net", "_c_position"))
    assert hit["hint"] == "message client→server"


def test_no_rpc_functions_no_message_nodes(tmp_path):
    graph = scan(tmp_path, {"a.gd": "extends Node\nfunc f():\n\tpass\n"})
    assert not [n for n in graph.nodes.values() if n.comm.get("type") == "rpc"]


def test_big_box_is_split_by_sending_function(tmp_path, monkeypatch):
    monkeypatch.setattr("coderstanding.rpc_messages.SPLIT_BOX_ROWS", 3)
    names = [f"_m{i}" for i in range(5)]
    handler = "class_name Hub\nextends Node\n" + "".join(f"@rpc\nfunc {n}():\n\tpass\n" for n in names)
    sender = ("class_name Src\nextends Node\nvar hub: Hub\n"
              "func big():\n" + "".join(f"\thub.{n}.rpc()\n" for n in names[:4])
              + "func small():\n\thub.%s.rpc()\n\thub.%s.rpc()\n" % (names[3], names[4]))
    graph = scan(tmp_path, {"hub.gd": handler, "src.gd": sender})
    big, small = (graph.nodes[message_group_id("Hub", "Src", f)] for f in ("big", "small"))
    assert big.name == "Hub messages ← Src#big"
    assert [m.name for m in big.members] == names[:4]  # _m3 is sent by both: the busier function wins
    assert [m.name for m in small.members] == [names[4]]
    assert message_group_id("Hub", "Src") not in graph.nodes
    assert {c.member for c in graph.edges[("Src", big.id, "dependency")].calls} == set(names[:4])
    assert set(graph.nodes[ "Hub"].members and message_directions(graph, graph.nodes["Hub"])) == set(names)
