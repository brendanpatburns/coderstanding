"""Network messages (remote calls, RPC) as communications nodes (DESIGN 8).

A remote-callable function (a member with `rpc` set: GDScript `@rpc`) is a message. The
messages of one handling class are split by who sends them: each (handler, sender class) pair
gets one `rpc` comm node, `comm:<handler>#<sender>` ("Net messages ← Chat"), whose rows
(function members) are the messages that sender sends to that handler. Messages nobody sends
(or whose sender is unknown) share `comm:<handler>#(not sent)` ("Net messages (not sent)"). A
message sent from two classes is a row in both boxes. A box opens like a class:
sender -> message -> handler.
A box with more than SPLIT_BOX_ROWS rows is split again by the sending function:
`comm:<handler>#<sender>#<function>` ("Net messages ← Chat#sync_world"). A message sent from
several functions of that sender goes in the box of the function that sends the most messages
of the box (ties: the first name), so every message stays in exactly one box and the biggest
senders keep their messages together.
Senders are found by a text pass over the source files, like the HTTP clients (literals and
names only, no data flow): every mention of a message's name except its definition and plain
local calls `f(...)`. The direction comes from the remote-call mode, never from the name."""

import re
from collections import defaultdict

from .filewalk import list_files
from .model import FUNCTION_KINDS, INIT_MEMBER, Call, Edge, Location, Member, is_comm
from .stack import CommUse, OwnerIndex, comm_id, comm_node, link_caller

RPC_TYPE = "rpc"
CLIENT_TO_SERVER, SERVER_TO_CLIENT, ANY_DIRECTION = "client→server", "server→client", "any"


def mode_words(mode):
    """`any_peer call_local` or C#-style `AnyPeer CallLocal` -> {"any_peer", "call_local"}."""
    return {re.sub(r"(?<=[a-z])(?=[A-Z])", "_", word).lower() for word in mode.split()}


def rpc_direction(mode, broadcast=False):
    """Who sends a message, from its remote-call mode (Godot's rule, as in C#'s `RpcMode`):
    `any_peer` lets any peer call it, so clients send it to the server (or to everyone: `any`
    when some sender broadcasts it with `.rpc()`); otherwise only the authority (the server)
    may call it, so the server sends it to clients."""
    if "any_peer" in mode_words(mode):
        return ANY_DIRECTION if broadcast else CLIENT_TO_SERVER
    return SERVER_TO_CLIENT


def is_message_group(node):
    return is_comm(node) and node.comm.get("type") == RPC_TYPE


SPLIT_BOX_ROWS = 25  # a (handler, sender) box with more rows is split by the sending function
NOT_SENT = "(not sent)"  # the id part and name suffix of a handler's box of messages with no known sender


def box_label(sender_id, function=""):
    """The sender part of a box id: the sender class, plus `#function` in a split box."""
    return f"{sender_id}#{function}" if function else sender_id


def message_group_id(handler_id, sender_id=NOT_SENT, function=""):
    return comm_id(handler_id, RPC_TYPE, box_label(sender_id, function))


def message_groups_of(graph, handler_id):
    """Every message box of one handling class (one per sender, plus the not-sent one)."""
    return [node for node in graph.nodes.values()
            if is_message_group(node) and node.comm["owner"] == handler_id]


def function_at(node, line):
    """The innermost function of the node whose lines hold `line`, else None."""
    inside = [m for m in node.members if m.kind in FUNCTION_KINDS and m.line <= line <= (m.end or m.line)]
    return min(inside, key=lambda m: (m.end or m.line) - m.line).name if inside else None



# --- reading senders: GDScript ---

def code_parts(line):
    """(string literal spans [(start, end)] with their quotes, comment start or len(line))."""
    spans, quote, start, i = [], "", 0, 0
    while i < len(line):
        char = line[i]
        if quote:
            if char == "\\":
                i += 1
            elif char == quote:
                spans.append((start, i + 1))
                quote = ""
        elif char in "\"'":
            quote, start = char, i
        elif char == "#":
            return spans, i
        i += 1
    return spans, len(line)


def gdscript_mention(line, start, end, spans):
    """(is a send, broadcast) for a message name found at line[start:end]."""
    for first, last in spans:
        if first <= start < last:  # in a string: only `"name"` itself names the message
            exact = first + 1 == start and end == last - 1
            return exact, exact and bool(re.search(r"\brpc\(\s*&?$", line[:first]))
    after = line[end:]
    if re.search(r"\bfunc\s+$", line[:start]) or re.match(r"\s*\(", after):
        return False, False  # its definition, or a plain local call
    return True, bool(re.match(r"\s*\.rpc\(", after))


def gdscript_sends(text, pattern):
    """(name, line, broadcast) for every send of a message in one GDScript file."""
    for number, line in enumerate(text.splitlines(), 1):
        if not pattern.search(line):
            continue
        spans, comment = code_parts(line)
        for found in pattern.finditer(line, 0, comment):
            sends, broadcast = gdscript_mention(line, found.start(), found.end(), spans)
            if sends:
                yield found.group(), number, broadcast


SEND_READERS = {"gd": gdscript_sends}  # file extension -> sender reader (other languages plug in here)


# --- building the nodes ---

def find_handlers(graph):
    """message name -> [(handler node, its member)] over every remote-callable function."""
    handlers = defaultdict(list)
    for node in graph.nodes.values():
        if node.kind == "external":
            continue
        for member in node.members:
            if member.rpc and member.kind in FUNCTION_KINDS:
                handlers[member.name].append((node, member))
    return handlers


def pick_handler(handlers, name, sender, used):
    """The one class handling `name` for this sender: the only one, else the sender's own, else
    the only one the sender already has a line to (`used`: class id -> ids it points at)."""
    options = [node for node, _ in handlers[name]]
    if len(options) == 1:
        return options[0]
    if sender in options:
        return sender
    known = [node for node in options if node.id in used[sender.id]]
    return known[0] if len(known) == 1 else None


def read_sends(root, graph, skip_dirs, handlers):
    """(sends, broadcast names, unresolved count): sends = sender id -> handler id ->
    (calling function, message) -> [Location]."""
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, sorted(handlers, key=len, reverse=True)))
                         + r")(?!\w)")
    owners = OwnerIndex(graph)
    used = defaultdict(set)
    for edge in graph.edges.values():
        used[edge.from_id].add(edge.to)
    sends = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    broadcast, unresolved = set(), 0
    for rel in list_files(root, [f"*.{ext}" for ext in SEND_READERS], skip_dirs):
        reader = SEND_READERS[rel.rsplit(".", 1)[-1]]
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, line, is_broadcast in reader(text, pattern):
            sender = owners.owner(rel, line)
            handler = pick_handler(handlers, name, sender, used) if sender else None
            if handler is None:
                unresolved += 1
                continue
            calling = function_at(sender, line) or INIT_MEMBER
            sends[sender.id][handler.id][(calling, name)].append(Location(rel, line))
            if is_broadcast:
                broadcast.add((handler.id, name))
    return sends, broadcast, unresolved


def add_message_group(graph, handler, sender, members, broadcast, function=""):
    """One box of the handler's messages (rows = the messages `sender` sends it, or only the ones
    sent by its `function`; `sender` None = the ones with no known sender) and its line to the
    handler, one call per message."""
    sender_id = sender.id if sender else NOT_SENT
    use = CommUse(messages=[m.name for m in members],
                  at=sorted(Location(handler.file, m.line) for m in members))
    node = comm_node(handler, RPC_TYPE, None, use, label=box_label(sender_id, function))
    node.name = f"{handler.name} messages ← {box_label(sender.name, function)}" if sender else f"{handler.name} messages {NOT_SENT}"
    node.members = [Member(m.name, "function", "", m.line, m.end, m.rpc) for m in members]
    node.comm["directions"] = {m.name: rpc_direction(m.rpc, (handler.id, m.name) in broadcast)
                               for m in members}
    graph.add_node(node)
    calls = [Call(m.name, 1, [Location(handler.file, m.line)], m.name) for m in members]
    graph.add_edge(Edge(node.id, handler.id, "dependency", detail=[RPC_TYPE],
                        at=[c.at[0] for c in calls], calls=calls))
    return node


def senders_by_message(sends):
    """(handler id, message name) -> [sender id] over every send."""
    senders = defaultdict(list)
    for sender_id, per_handler in sends.items():
        for handler_id, per_call in per_handler.items():
            for _, name in per_call:
                if sender_id not in senders[(handler_id, name)]:
                    senders[(handler_id, name)].append(sender_id)
    return senders


def functions_by_message(sends):
    """(handler id, sender id, message name) -> {sending functions} over every send."""
    functions = defaultdict(set)
    for sender_id, per_handler in sends.items():
        for handler_id, per_call in per_handler.items():
            for calling, name in per_call:
                functions[(handler_id, sender_id, name)].add(calling)
    return functions


def split_by_function(members, functions_of):
    """{sending function: [members]} for one big box; `functions_of`: message name -> the
    functions that send it. Each message goes to its busiest sending function."""
    busy = defaultdict(int)
    for member in members:
        for function in functions_of[member.name]:
            busy[function] += 1
    split = defaultdict(list)
    for member in members:
        split[min(functions_of[member.name], key=lambda function: (-busy[function], function))].append(member)
    return split


def add_rpc_nodes(root, graph, skip_dirs):
    """Add the message boxes (one per handler and sender) and the sender -> message -> handler
    lines. Returns {"messages", "boxes", "sends", "unresolved"} counts."""
    handlers = find_handlers(graph)
    if not handlers:
        return {"messages": 0, "boxes": 0, "sends": 0, "unresolved": 0}
    sends, broadcast, unresolved = read_sends(root, graph, skip_dirs, handlers)
    senders = senders_by_message(sends)
    functions = functions_by_message(sends)
    by_pair = defaultdict(list)  # (handler id, sender id or None) -> members
    for options in handlers.values():
        for node, member in options:
            for sender_id in senders.get((node.id, member.name)) or [None]:
                by_pair[(node.id, sender_id)].append(member)
    by_box = {}  # (handler id, sender id or None, sending function or "") -> members
    box_of = {}  # (handler id, sender id, message name) -> sending function or ""
    for (handler_id, sender_id), members in by_pair.items():
        if sender_id and len(members) > SPLIT_BOX_ROWS:
            functions_of = {m.name: functions[(handler_id, sender_id, m.name)] for m in members}
            parts = split_by_function(members, functions_of)
        else:
            parts = {"": members}
        for function, part in parts.items():
            by_box[(handler_id, sender_id, function)] = part
            for member in part:
                box_of[(handler_id, sender_id, member.name)] = function
    for (handler_id, sender_id, function), members in sorted(by_box.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2])):
        add_message_group(graph, graph.nodes[handler_id], graph.nodes.get(sender_id),
                          sorted(members, key=lambda m: m.line), broadcast, function)
    for sender_id, per_handler in sorted(sends.items()):
        for handler_id, per_call in sorted(per_handler.items()):
            per_box = defaultdict(dict)
            for (calling, name), locs in per_call.items():
                per_box[box_of[(handler_id, sender_id, name)]][(calling, name)] = locs
            for function, box_calls in sorted(per_box.items()):
                link_caller(graph, sender_id, message_group_id(handler_id, sender_id, function), box_calls, RPC_TYPE)
    return {"messages": len({(node.id, member.name) for options in handlers.values() for node, member in options}),
            "boxes": len(by_box),
            "sends": sum(len(locs) for per_handler in sends.values() for per_call in per_handler.values()
                         for locs in per_call.values()),
            "unresolved": unresolved}


# --- reading them back (view engine, text) ---

def message_directions(graph, node):
    """message name -> direction for a message node, or for the class that handles them."""
    if is_message_group(node):
        return node.comm.get("directions", {})
    found = {name: direction for group in message_groups_of(graph, node.id)
             for name, direction in group.comm.get("directions", {}).items()}
    return {m.name: found[m.name] for m in node.members if m.name in found}  # in the class's own order


def node_messages(graph, node, outgoing):
    """{"handles": [{name, direction}], "sends": [{name, direction, to}]} of a class;
    `outgoing`: its links (views.Link or Edge, with `to` and `calls`)."""
    handles = [{"name": name, "direction": direction}
               for name, direction in message_directions(graph, node).items()] \
        if not is_message_group(node) else []
    sends = {}
    for link in outgoing:
        target = graph.nodes.get(link.to)
        if target is None or not is_message_group(target) or target is node:
            continue
        directions = target.comm.get("directions", {})
        for call in link.calls:
            sends.setdefault(call.member, {"name": call.member, "direction": directions.get(call.member, ""),
                                           "to": target.comm["owner"]})
    return {"handles": handles, "sends": sorted(sends.values(), key=lambda s: s["name"])}


def message_hint(graph, node, member):
    """`message client→server` for a remote-callable function, else ""."""
    direction = message_directions(graph, node).get(member.name) if member.rpc else None
    return f"message {direction}" if direction else ""
