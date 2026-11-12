"""Slim text output (docs/DESIGN.md 11), built on the view engine's `compute_view` dict.

Terse on purpose: an AI reads it instead of source files to save tokens.
"""

import shlex
from collections import Counter

from .checks import STALE_KIND, layer_label
from .e2e_checks import E2E_RUNNER, only_word
from .filekinds import CODE
from .items import plain_links
from .notes import ARROW, FUNCTION_SEP
from .planned import MANUAL, PLANNED
from .views import ALL_LINES, LINES, NickNamer, compute_view, display_name, level_label

FEW_CALLS = 3
DESCRIPTION_MAX = 200  # longest edge description printed under a zoom line
QUIET_FUNCTIONS = 8  # functions without calls named on the `no calls:` line


def node_label(node, name):
    marks = "*" if "singleton" in node["flags"] else ""
    if node["kind"] == "interface":
        marks += " [I]"
    elif node["kind"] == "external":
        marks += f" [{node['dep'] or 'outside'}]"
    elif node["kind"] == "scene":
        marks += " [scene]"
    elif node.get("file_kind") not in (None, "", CODE):
        marks += f" [{node['file_kind'].lower()}]"  # code is the default: only other kinds are marked
    if node.get("made"):
        marks += f" [{node['made']}]"  # planned: not in the code; manual: real, the scan can't see it
    return name + marks


def unique_names(nodes, name_of):
    counts = {}
    for node in nodes:
        counts[name_of(node)] = counts.get(name_of(node), 0) + 1
    return {n["id"]: name_of(n) for n in nodes if counts[name_of(n)] == 1}


def labels(view):
    """id -> printed name: the short name, else the file name, else the id where names clash;
    with its nickname (display_name). (id, function) -> the printed name of a nicknamed
    function of an open class (end_text)."""
    nodes = view["nodes"]
    names = {n["id"]: n["id"] for n in nodes}
    names.update(unique_names(nodes, lambda n: n["file"].rsplit("/", 1)[-1] or n["id"]))
    names.update(unique_names(nodes, lambda n: n["name"]))
    found = {n["id"]: node_label(n, display_name(names[n["id"]], n.get("title"))) for n in nodes}
    for node_id, parts in (view.get("expanded") or {}).items():
        found.update({(node_id, row["name"]): display_name(row["name"], row["nickname"])
                      for row in parts["functions"] if row.get("nickname")})
    return found


def calls_text(calls, all_calls=False):
    names = list(dict.fromkeys(c["member"] for c in calls))  # one per member, not per caller
    if not names:
        return ""
    shown = names if all_calls else names[:FEW_CALLS]
    more = len(names) - len(shown)
    return " {" + ", ".join(shown) + (f" +{more}" if more else "") + "}"


def stale_mark(item, field):
    """` (stale)` after a note whose code changed since it was written (DESIGN 10), else nothing."""
    return " (stale)" if field in item.get("stale", ()) else ""


def arrow(edge):
    """`>` a line in the code, `~>` through hidden classes, `..>` a planned line (not in the code)."""
    if PLANNED in edge["kinds"]:
        return "..>"
    return ">" if edge["direct"] else "~>"


def edge_line(edge, names, all_calls=False, short_name=str):
    via = "" if edge["direct"] or not edge["via"] else \
        f" (via {min(len(p) for p in edge['via'])})"
    text = edge["hint"] and edge["hint"] + stale_mark(edge, "hint") or f"[{', '.join(edge['kinds'])}]"
    if edge["as"]:
        text = f"as {short_name(edge['as'])}: {text}"
    return (f"{names[edge['from']]} {arrow(edge)} {names[edge['to']]}{via}: {text}"
            f"{calls_text(edge['calls'], all_calls)}")


def out_line(view, names):
    faded = [f"{names[e['from']]}{arrow(e)}{names[e['to']]}"
             for e in view["edges"] if e["faded"]]
    return ["out: " + ", ".join(faded)] if faded else []


def near_line(view, names):
    """`near:` the faded classes of a focus (one connection out), names only."""
    near = sorted(names[n["id"]] for n in view["nodes"] if n["faded"])
    return ["near: " + ", ".join(near)] if near else []


def alone_line(view, names):
    linked = {i for e in view["edges"] for i in (e["from"], e["to"])}
    alone = [names[n["id"]] for n in view["nodes"] if n["id"] not in linked]
    return ["alone: " + ", ".join(alone)] if alone else []


def review_line(item, indent="  "):
    """The item's review joined on one `review:` line, cut like a description; [] if none."""
    text = " ".join((item.get("review") or "").split())
    if not text:
        return []
    return [f"{indent}review{stale_mark(item, 'review')}: " + (text if len(text) <= DESCRIPTION_MAX
                                   else text[:DESCRIPTION_MAX - 1].rstrip() + "…")]


def description_line(edge):
    """The edge's description on one indented line, cut to DESCRIPTION_MAX chars."""
    text = " ".join(edge["description"].split())
    if len(text) > DESCRIPTION_MAX:
        text = text[:DESCRIPTION_MAX - 1].rstrip() + "…"
    return "  " + text + stale_mark(edge, "description")


def edge_lines(view, all_calls=False, short_name=str, descriptions=False, faded=True):
    names = labels(view)
    order = {n["id"]: i for i, n in enumerate(view["nodes"])}
    edges = sorted((e for e in view["edges"] if not e["faded"]),  # explained lines first
                   key=lambda e: (bool(e.get("quiet")), order[e["from"]], order[e["to"]]))
    lines = []
    for edge in edges:
        lines.append(edge_line(edge, names, all_calls, short_name))
        if descriptions and edge.get("description"):
            lines.append(description_line(edge))
        if descriptions:
            lines += review_line(edge)
    if view.get("focus"):  # DESIGN 7c: a focus lists the faded classes, not every faded line
        return lines + (near_line(view, names) if faded else [])
    return lines + (out_line(view, names) if faded else [])


def hub_mark(degree):
    return f" [hub {degree}]"


def hubs_text(graph, short_name=str, shown=6):
    """` (hubs ≥ 24: net [hub 80], world [hub 41], +3)` (busiest first), or '' when none."""
    hubs = graph.hubs
    ids = hubs.get("ids", [])
    if not ids:
        return ""
    names = [short_name(i) + hub_mark(graph.nodes[i].hub_degree) for i in ids[:shown]]
    if len(ids) > shown:
        names.append(f"+{len(ids) - shown}")
    return f" (hubs ≥ {hubs['threshold']}: {', '.join(names)})"


def overview_lines(overview):
    """Level 0's 30-second overview: the flow sentence, then one line per domain (what it is for,
    key classes, who it talks to). No sentence note yet: the generated one, marked."""
    if not overview:
        return []
    sentence = overview["sentence"]
    mark = " (AI draft)" if overview["ai"] else "" if sentence else " (generated, none written)"
    lines = [f"overview{mark}: {sentence or overview['generated']}"]
    for domain in overview["domains"]:
        what = hint_suffix(domain["hint"] or domain["description"])
        keys = ", ".join(display_name(key["name"], key["title"]) for key in domain["keys"])
        talks = ", ".join(row["to"] + (f" ({row['hint']})" if row["hint"] else "")
                          for row in domain["talks"])
        lines.append(f"  {domain['name']} ({domain['classes']}){what}"
                     + (f" | key: {keys}" if keys else "") + (f" | talks to: {talks}" if talks else ""))
    return lines


def format_graph(app, view, all_calls=False, short_name=str, graph=None):
    counts = view["counts"]
    header = f"{app} {level_label(view.get('scale'), view['level'])} {view['view']} {counts['shown']}/{counts['total']}"
    header += hubs_text(graph, short_name) if graph else ""
    header += plan_text(view.get("plan"))
    return "\n".join([header, *overview_lines(view.get("overview")), *edge_lines(view, all_calls, short_name),
                      *alone_line(view, labels(view)), *comm_lines(view, labels(view))])


MAX_RPCS_SHOWN = 8
RPC_TYPE = "rpc"  # rpc_messages.RPC_TYPE: a message node's comm type


def capped_list(items, limit=None):
    """`a, b, c +N` (all of them when no limit)."""
    shown = items if limit is None else items[:limit]
    more = len(items) - len(shown)
    return ", ".join(shown) + (f" +{more}" if more > 0 else "")


def message_text(message, arrow=""):
    """`→ _c_position (client→server)`: a message, its direction, and an arrow for sent (→) or
    handled (←)."""
    return f"{arrow + ' ' if arrow else ''}{message['name']} ({message['direction']})"


def group_messages(comm):
    """The messages of a message node as message dicts, in their order."""
    return [{"name": name, "direction": comm["directions"].get(name, "")} for name in comm["messages"]]


def message_lines(node, limit=None):
    """`sends: → a (client→server)`, `handles: ← b (server→client)` for a class; for a message node
    `messages (N): a (client→server), ...`."""
    comm = node.get("comm") or {}
    if comm.get("type") == RPC_TYPE:
        found = group_messages(comm)
        return [f"messages ({len(found)}): " + capped_list([message_text(m) for m in found], limit)]
    messages = node.get("messages") or {}
    return [f"{label}: " + capped_list([message_text(m, arrow) for m in messages[key]], limit)
            for label, key, arrow in (("sends", "sends", "→"), ("handles", "handles", "←"))
            if messages.get(key)]


def comm_lines(view, names):
    """`comm <endpoint>: owner Net, listens, messages a, b` per communications node on screen
    (a message node: `handler Net, messages (N): a (client→server), ... +N`), and
    `rpc <class>: f, g +N` per class with `@rpc` functions."""
    lines = []
    for node in view["nodes"]:
        comm = node.get("comm")
        if comm and comm.get("type") == RPC_TYPE:
            lines.append(f"comm {names[node['id']]}: handler {comm['owner']}, "
                         + message_lines(node, MAX_RPCS_SHOWN)[0])
        elif comm:
            parts = [f"owner {comm['owner']}", comm["role"], *(["messages " + ", ".join(comm["messages"])]
                                                              if comm["messages"] else [])]
            lines.append(f"comm {names[node['id']]}: " + ", ".join(p for p in parts if p))
        rpcs = node.get("rpcs") or []
        if rpcs:
            lines.append(f"rpc {names[node['id']]}: " + capped_list(rpcs, MAX_RPCS_SHOWN))
    return lines


def rules_lines(label, rules):
    """`rules X: first line`, further lines indented; nothing when there are no rules."""
    lines = [line for line in (rules or "").splitlines() if line.strip()]
    if not lines:
        return []
    return [f"{label}: {lines[0]}", *("  " + line for line in lines[1:])]


def format_zoom(app, view, all_calls=False, short_name=str):
    zoom, names = view["zoom"], labels(view)
    header = f"{app} zoom {names[zoom['from']]}>{names[zoom['to']]} {level_label(view.get('scale'), view['level'])} {view['view']}"
    by_id = {n["id"]: n for n in view["nodes"]}
    rules = [line for end in dict.fromkeys((zoom["from"], zoom["to"])) if end in by_id
             for line in rules_lines(f"rules {names[end]}", by_id[end].get("rules"))]
    return "\n".join([header, *rules, *edge_lines(view, all_calls, short_name, descriptions=True)])


def size_text(node):
    """`120 lines, 8 members`; `(file)` when the lines are the whole file's."""
    if not node.get("lines"):
        return ""
    text = f"{node['lines']} lines" + ("" if node.get("span") else " (file)")
    return text + (f", {node['members']} members" if node.get("members") else "")


def marks_text(node):
    """Status and tags: `[wip] #net #audio`."""
    marks = [f"[{node['status']}]"] if node.get("status") else []
    return " ".join(marks + [f"#{tag}" for tag in node.get("tags") or []])


MAX_TESTS_SHOWN = 8
MAX_MESSAGES_SHOWN = 12  # per sends / handles line of a class (its message node lists all)


def plan_mark(item):
    """`[todo] work:high imp:low §spec` of a planned item ('' for other items)."""
    if not item.get("stage"):
        return ""
    stage = item["stage"] + (" (spec)" if item.get("stage_from") == "spec" else "")
    parts = [f"[{stage}]"] + [f"{label}:{item[key]}" for key, label in (("priority", "work"), ("importance", "imp"))
                              if item.get(key)]
    parts += [f"§{item['spec']}"] if item.get("spec") else []
    parts += ["(AI draft)"] if item.get("made_ai") else []
    return " ".join(parts)


def function_mark(row):
    """` [stub]` / ` [planned todo]` after a function name ('' for a built one)."""
    if row.get("made") == PLANNED:
        return f" [planned {row.get('stage') or ''}]".replace(" ]", "]")
    return " [stub]" if row.get("stub") else ""


def plan_text(counts):
    """` plan: 2 planned, 1 manual, 3 planned functions, 4 stubs` for a graph header, '' when none."""
    words = (("planned", "planned"), ("manual", "manual"), ("functions", "planned function"), ("stubs", "stub"))
    parts = [f"{counts[key]} {label}" + ("s" if counts[key] > 1 and label not in ("planned", "manual") else "")
             for key, label in words if counts and counts.get(key)]
    return f" plan: {', '.join(parts)}" if parts else ""


def plan_detail_lines(node):
    """A hand-made class's lines in `node`: what it is, its plan, where it lives, real matches."""
    if node.get("made") == PLANNED:
        lines = ["NOT IN CODE (planned: build it, don't look for its file) " + plan_mark(node)]
        exact = (node.get("matches") or {}).get("exact")
        if exact:
            lines.append(f"built now? real code has this name: {', '.join(exact)} "
                         f"(link with `plan --link {node['id']} <id>`)")
        return lines
    if node.get("made") == MANUAL:
        return [f"added manually (real, the scan can't see it); where: {node.get('where') or 'unknown'}"]
    return []


def node_detail(node):
    """A class: kind, file:line, level, domain, size, status/tags, hint; then warnings,
    tests, rules and description."""
    at = (node.get("comm") or {}).get("at") if not node["file"] else None  # a comm node: its owner's place
    place = f"{at['file']}:{at['line']}" if at else f"{node['file']}:{node['line']}"
    parts = [f"[{node['kind']}]", place, f"L{node['level']}"]
    if node.get("file_kind") not in (None, "", CODE):
        parts.append(f"file-kind={node['file_kind']}")
    if node.get("hub_degree"):
        parts.append(hub_mark(node["hub_degree"]).strip())
    if node.get("domain"):
        parts.append(f"domain={node['domain']}" + (" (rule)" if node.get("domain_by") == "rule" else ""))
    parts += [text for text in (size_text(node), marks_text(node)) if text]
    lines = [" ".join(parts) + (f": {node['hint']}{stale_mark(node, 'hint')}" if node.get("hint") else "")]
    lines += plan_detail_lines(node)
    if node.get("warnings"):
        lines.append("warn: " + ", ".join(w["text"] for w in node["warnings"]))
    if node.get("ignored_warnings"):
        lines.append("ignored: " + ", ".join(f"{w['text']} ({w['reason']})"
                                             for w in node["ignored_warnings"]))
    if node.get("tested_by"):
        lines.append("tests: " + ", ".join(node["tested_by"][:MAX_TESTS_SHOWN])
                     + (f" +{len(node['tested_by']) - MAX_TESTS_SHOWN} more{TESTS_HINT}"
                        if len(node["tested_by"]) > MAX_TESTS_SHOWN else ""))
    if node.get("loaders"):
        lines.append("loaded by: " + ", ".join(
            l["name"] + (f" ({l['how']})" if l["how"] else "") for l in node["loaders"]))
    lines += message_lines(node, None if node.get("comm") else MAX_MESSAGES_SHOWN)
    lines += rules_lines("rules" + stale_mark(node, "rules"), node.get("rules"))
    lines += review_line(node, "")
    description = node.get("description")
    return lines + ([description + stale_mark(node, "description")] if description else [])


def node_head(app, view, node_id, names, suffix=""):
    node = next(n for n in view["nodes"] if n["id"] == node_id)
    return [f"{app} node {names[node_id]} {level_label(view.get('scale'), view['level'])} {view['view']}{suffix}",
            *node_detail(node)]


def format_node(app, view, node_id, all_calls=False, short_name=str):
    return "\n".join([*node_head(app, view, node_id, labels(view)),
                      *edge_lines(view, all_calls, short_name)])


# --- functions of expanded classes (DESIGN 12c) ---

def counted_text(name, count):
    return name + (f" ×{count}" if count > 1 else "")


def function_label(names, node_id, fn):
    return names.get((node_id, fn), fn)


def end_text(names, node_id, fn):
    return f"{names[node_id]}#{function_label(names, node_id, fn)}" if fn else names[node_id]


def grouped_out(links, names, short_name):
    """Outgoing links of one endpoint, one text per target class: `media*: upload ×2, x`
    (a signal connection reads `signal → handler`)."""
    groups = {}
    for link in links:
        members = groups.setdefault((link["to"], link["as"], link["direct"]), {})
        for m in link["members"]:
            members[m["name"]] = members.get(m["name"], 0) + m["count"]
    texts = []
    for (to, as_, direct), members in groups.items():
        prefix = f"as {short_name(as_)}: " if as_ else ""
        called = ", ".join(counted_text(n, c) for n, c in members.items())
        texts.append((">" if direct else "~>", f"{names[to]}: {prefix}{called}".rstrip(": ")))
    return texts


def callers_text(links, names):
    """`B#f ×2, C`: one entry per calling end, direct and derived lines added up."""
    counts = {}
    for link in links:
        end = end_text(names, link["from"], link["from_fn"])
        counts[end] = counts.get(end, 0) + link["count"]
    return ", ".join(counted_text(end, n) for end, n in counts.items())


def function_lines(view, node_id, names, short_name=str, only=None):
    """Per function of an open class: `A#f > B: calls` lines and an `in:` line of callers
    (`A#f in: ...` when it calls nothing in view);
    then `A (fields) > B` lines for links with no function; `no calls:` for the rest."""
    fan = view["fanout"]
    lines, quiet = [], []
    for row in view["expanded"][node_id]["functions"]:
        name = row["name"]
        if only not in (None, name):
            continue
        out = [l for l in fan if l["from"] == node_id and l["from_fn"] == name]
        callers = [l for l in fan if l["to"] == node_id and l["to_fn"] == name
                   and not (l["internal"] and l["from_fn"] == name)]
        head = end_text(names, node_id, name) + function_mark(row)
        lines += [f"{head} {arrow} {text}" for arrow, text in grouped_out(out, names, short_name)]
        if callers:  # under its call lines, else on the function's own line
            lines.append(("  " if out else head + " ") + "in: " + callers_text(callers, names))
        if not out and not callers:
            quiet.append(name + function_mark(row))
    if only is None:
        for link in fan:
            if link["from"] == node_id and not link["from_fn"] and not link["internal"]:
                called = ", ".join(counted_text(m["name"], m["count"]) for m in link["members"])
                target = end_text(names, link["to"], link["to_fn"])
                lines.append(f"{names[node_id]} (fields) {arrow(link)} "
                             f"{target}: [{', '.join(link['kinds'])}] {called}".rstrip())
        if quiet:
            more = len(quiet) - QUIET_FUNCTIONS
            lines.append("no calls: " + ", ".join(function_label(names, node_id, n) for n in quiet[:QUIET_FUNCTIONS])
                         + (f" +{more}" if more > 0 else ""))
    return lines


def format_functions(app, view, node_id, short_name=str):
    """`node --functions`: the class, then its function lines."""
    names = labels(view)
    return "\n".join([*node_head(app, view, node_id, names, " functions"),
                      *function_lines(view, node_id, names, short_name)])


# --- the whole screen as text (`coderstanding view`, the viewer's Copy for AI) ---

def view_command(app, level=1, concrete=False, show=None, zoom=None, expand=(), select=None,
                 focus=(), domain=None):
    """The `coderstanding view` command line that prints this text again."""
    show = show or {}
    parts = ["coderstanding", "view", app, "--level", str(level)]
    if concrete:
        parts.append("--concrete")
    if zoom:
        parts += ["--zoom", zoom["from"]] + ([zoom["to"]] if zoom.get("to") else [])
        for hidden in zoom.get("via") or []:
            parts += ["--via", hidden]
        if zoom.get("level", level) != level:
            parts += ["--zoom-level", str(zoom["level"])]
    elif domain:
        parts += ["--domain", domain]
    if focus:
        parts += ["--focus", *focus]
    for node_id in expand:
        parts += ["--expand", node_id]
    if select:
        parts += ["--select", select]
    for hidden in show.get("hide_types") or []:
        parts += ["--hide-type", hidden]
    for hidden in show.get("hide_file_kinds") or []:
        parts += ["--hide-file-kind", hidden]
    for hidden in show.get("hide_kinds") or []:
        parts += ["--hide-kind", hidden]
    for tag in show.get("tags") or []:
        parts += ["--tag", tag]
    for hidden in show.get("hide_tags") or []:
        parts += ["--hide-tag", hidden]
    if show.get(LINES) == ALL_LINES:
        parts += ["--lines", ALL_LINES]
    flags = [("--tests", show.get("test")), ("--no-external", show.get("external") is False),
             ("--no-comm", show.get("comm") is False), ("--no-planned", show.get(PLANNED) is False)]
    parts += [flag for flag, on in flags if on]
    return "$ " + " ".join(shlex.quote(p) for p in parts)


def note_lines(item, names=("hint", "description", "rules", "review")):
    lines = []
    for name in names:
        if item.get(name):
            lines += rules_lines(name + stale_mark(item, name), item[name])
    return lines


def selection_lines(view, select, names, short_name):
    """A short block for the selected class (`id`), connection (`A→B`) or function (`A#f`)."""
    node = next((n for n in view["nodes"] if n["id"] == select), None)
    if node:
        return [f"selected {names[select]}:", *node_detail(node)]
    edge = next((e for e in view["edges"] if e["key"] == select), None)
    if edge:
        return ["selected:", edge_line(edge, names, True, short_name),
                *([description_line(edge)] if edge.get("description") else []),
                *review_line(edge)]
    node_id, _, fn = select.rpartition("#")
    row = next((f for f in view["expanded"].get(node_id, {}).get("functions", [])
                if f["name"] == fn), None)
    if row is None:
        return []
    return [f"selected {names[node_id]}#{fn}:", *note_lines(row),
            *function_lines(view, node_id, names, short_name, only=fn)]


def format_view(app, view, command, select=None, short_name=str):
    """The command, the graph/zoom/node text, function lines per open class, the selection."""
    zoom = view["zoom"]
    if zoom and zoom.get("to"):
        body = format_zoom(app, view, short_name=short_name)
    elif zoom:
        body = format_node(app, view, zoom["from"], short_name=short_name)
    else:
        body = format_graph(app, view, short_name=short_name)
    names = labels(view)
    lines = [command, body]
    for name in ("tags", "kinds", "file_kinds", "types"):  # inserted at 1, so "hidden types:" ends up first
        hidden = view.get("hidden_" + name, [])
        if hidden:
            lines.insert(1, f"hidden {name.replace('_', ' ')}: " + " ".join(hidden))
    for node_id in view["expanded"]:
        lines += function_lines(view, node_id, names, short_name)
    if select:
        lines += selection_lines(view, select, names, short_name)
    return "\n".join(lines)


def short_namer(graph, notes=None):
    """id -> the class's short name (a domain card's name), with its nickname given notes."""
    return NickNamer(lambda i: graph.nodes[i].name if i in graph.nodes else i.split(":", 1)[-1], notes, graph)


def view_text(app, graph, notes, *, level=1, view="all", show=None, zoom=None, expand=(),
              select=None, focus=(), domain=None):
    """What `coderstanding view` prints and /api/text returns: one place for both."""
    shown = compute_view(graph, notes, level=level, view=view, show=show, zoom=zoom,
                         expand=expand, focus=focus, domain=domain)
    command = view_command(app, level, view == "concrete", show, zoom, expand, select, focus, domain)
    return format_view(app, shown, command, select, short_namer(graph, notes))


def layer_rule_text(number, layer):
    """`3. src/ui forbid src/net kind=call - why` (an architecture rule in the `rules` list)."""
    return f"{number}. {layer_label(layer)}" + (f" - {layer['why']}" if layer.get("why") else "")


def warning_group_lines(kind, items, short_name):
    def name(node_id, warning):
        return short_name(node_id) + (f" (ignored: {warning['reason']})" if warning.get("ignored") else "")

    if kind == "cycle":
        groups = {}
        for node_id, warning in items:
            groups.setdefault(warning["group"], []).append(name(node_id, warning))
        return [f"cycle {len(names)}: {' '.join(sorted(names))}"
                for _, names in sorted(groups.items(), key=lambda g: (-len(g[1]), g[0]))]
    if kind == "god":
        threshold = items[0][1]["threshold"]
        return [f"god (>= {threshold}): "
                + ", ".join(f"{name(i, w)} {w['degree']}" for i, w in items)]
    if kind == "layer":
        return [f"layer: {name(i, w)} > {w['to_name']} ({w['to_file']}) via {w['edge']} at "
                f"{w['file']}:{w['line']} [{w['rule']}]" + (f" - {w['why']}" if w["why"] else "")
                for i, w in items]
    counted = [f"{name(i, w)} [{', '.join(w['names'])}]" if "names" in w
               else f"{name(i, w)} {w['count']}" if "count" in w else name(i, w)
               for i, w in items]
    return [f"{kind}: {', '.join(counted)}"]


def stale_lines(items, short_name):
    """`stale: A hint, A#f review, A>B description`: notes whose code changed since written."""
    def label(key):
        kind, _, rest = key.partition(":")
        if kind == "edge":
            return ">".join(short_name(i) for i in rest.split(ARROW, 1))
        node_id, sep, fn = rest.partition(FUNCTION_SEP)
        return short_name(node_id) + sep + fn
    return ["stale (code changed since the note): "
            + ", ".join(f"{label(key)} {'/'.join(w['fields'])}" for key, w in items)]


def format_warnings(app, groups, short_name=str):
    """Every warning, one line per kind (one per cycle), header with the counts."""
    counts = ", ".join(f"{kind} {len(items)}" for kind, items in groups.items())
    lines = [f"{app} warnings: {counts}"]
    for kind, items in groups.items():
        if items:
            lines += (stale_lines(items, short_name) if kind == STALE_KIND
                      else warning_group_lines(kind, items, short_name))
    return "\n".join(lines)


def unresolved_name_lines(app, by_node, short_name=str):
    """`--names`: one line per node, `Node N: name count, name count`, biggest node first."""
    rows = sorted(by_node.items(), key=lambda row: (-sum(row[1].values()), row[0]))
    lines = [f"{app} unresolved names: {sum(sum(names.values()) for names in by_node.values())} refs "
             f"in {len(rows)} classes"]
    for node_id, names in rows:
        counted = ", ".join(f"{label} {count}" for label, count in names.most_common())
        lines.append(f"{short_name(node_id)} {sum(names.values())}: {counted}")
    return lines


OUTSIDE_SHOWN = 3  # names in the summary line; --all lists every stored name


def outside_lines(stack, show_all=False):
    """`outside: N refs (bpy 4, a.glb 2, ...)` from the stack data the viewer's Stack tab shows
    (host-program modules and asset files); `--all` adds one line per name. Empty when none."""
    outside = stack.get("outside") or {}
    top = outside.get("top") or []
    if not outside.get("refs"):
        return []
    shown = top if show_all else top[:OUTSIDE_SHOWN]
    more = len(top) - len(shown)
    names = ", ".join(f"{name} {count}" for name, count in shown) + (f", +{more} more (--all)" if more else "")
    return [f"outside (not in the repo on purpose): {outside['refs']} refs ({names})"]


# --- reading commands: outline, find, impact, brief (DESIGN 11) ---

MAX_BRIEF_NEIGHBOURS = 5
MAX_BRIEF_FUNCTIONS = 6
MAX_BRIEF_TESTS = 6
TESTS_HINT = " (`tests <app> <Name>` lists all)"
MAX_IMPACT_LINE = 40
MAX_NEAR_TESTS = 5  # impact's tests when none reaches the unit directly


def range_text(file, first, last=0):
    """`file:first-last` (`file:first` when the end is unknown)."""
    return f"{file}:{first}" + (f"-{last}" if last > first else "")


def hint_suffix(hint):
    return f"  — {hint}" if hint else ""


def outline_head(data):
    parts = [f"[{data['kind']}]", range_text(data["file"], data["line"], data["end"])]
    if data["lines"]:
        parts.append(f"{data['lines']} lines" + ("" if data["span"] else " (file)"))
    return " ".join(parts) + (f": {data['hint']}" if data["hint"] else "")


def function_text(row, owner=""):
    """`Owner#name -> Type  file:first-last  — hint` (`Owner#name` = a target for `read` etc.)."""
    name = (f"{owner}#" if owner else "") + display_name(row["name"], row.get("nickname")) \
        + (f" -> {row['type']}" if row["type"] else "")
    return f"  {name}  {range_text(row['file'], row['line'], row['end'])}{hint_suffix(row['hint'])}"


MAX_FORMS = 2
MAX_MISSING = 12
MAX_PLAIN_GLOBALS = 40
REACH_TEXT = {"autoload": "autoload", "class_name": "class_name", "global-class": "global class"}


def forms_text(label, forms):
    """`tests: root.get_node("Media")` (first MAX_FORMS forms, `+n` for the rest), or nothing."""
    if not forms:
        return []
    more = len(forms) - MAX_FORMS
    return [f"{label}: " + ", ".join(forms[:MAX_FORMS]) + (f" +{more}" if more > 0 else "")]


def global_line(row, here=None):
    """`Media  autoload res://src/media.gd · tests: root.get_node("Media")`; with `here` (the
    forms a file writes) `· here: ...`, or `· by name` when it writes none."""
    place = row["id"] if row["id"].startswith("res://") else row["file"]
    head = f"{row['name']}  {REACH_TEXT[row['kind']]} {place}"
    if here is not None:
        return " · ".join([head, *(forms_text("here", here) or ["by name"])])
    return " · ".join([head, *forms_text("tests", row["tests"]), *forms_text("code", row["code"])])


def globals_lines(rows):
    """One line per global; class_name scripts nobody looks up share one `class_name:` line."""
    plain = [r["name"] for r in rows if r["kind"] == "class_name" and not (r["tests"] or r["code"])]
    lines = [global_line(r) for r in rows if r["name"] not in plain]
    if plain:
        more = len(plain) - MAX_PLAIN_GLOBALS
        lines.append("class_name (by name): " + ", ".join(plain[:MAX_PLAIN_GLOBALS])
                     + (f" +{more} more (`outline` shows each)" if more > 0 else ""))
    return ["globals:", *("  " + line for line in lines)] if lines else []


def format_outline(app, data, function=None, short_name=None):
    owner = short_name(data["id"]) if short_name else data["name"]
    shown_function = function and display_name(function, data["functions"][0].get("nickname")
                                                if data["functions"] else None)
    lines = [f"{app} outline {display_name(data['name'], data.get('nickname'))}"
             + (f"#{shown_function}" if function else ""),
             outline_head(data)]
    if data.get("global"):
        lines.append("global: " + global_line(data["global"]))
    lines += globals_lines(data.get("globals", []))
    if data["functions"]:
        lines += ["functions:" if not function else "function:",
                  *(function_text(row, owner) for row in data["functions"])]
    if data["fields"]:
        lines += ["fields:", *(f"  {f['name']}{': ' + f['type'] if f['type'] else ''}  "
                               f"{range_text(f['file'], f['line'])}{'' if f.get('public', True) else '  [not public]'}"
                               for f in data["fields"])]
    if data["inner"]:
        lines += ["inside:", *(f"  {n['name']} [{n['kind']}]  "
                               f"{range_text(n['file'], n['line'], n['end'])}"
                               for n in data["inner"])]
    return "\n".join(lines)


def setup_function_line(row):
    return f"  {row['name']}{' -> ' + row['type'] if row['type'] else ''}  "\
           f"{range_text(row['file'], row['line'], row['end'])}{hint_suffix(row['hint'])}"


def format_setup(app, data):
    """What `coderstanding setup` prints: the file's hooks, helpers, bases, support nodes,
    globals and the fixtures its sibling tests have that it lacks."""
    head = f"{app} setup {data['file']}" + (f": {data['tests']} tests" if data["tests"] else "")
    lines = [head + (f", extends {data['extends']}" if data["extends"] else "")]
    if data["hooks"]:
        lines += ["hooks:", *map(setup_function_line, data["hooks"])]
    if data["helpers"]:
        lines += ["helpers:", *map(setup_function_line, data["helpers"])]
    for base in data["bases"]:
        names = [*(f"{n} (hook)" for n in base["hooks"]), *base["helpers"]]
        lines.append(f"base {base['name']} ({base['file']}): " + (", ".join(names) or "no helpers"))
    for support in data["support"]:
        lines.append(f"uses {support['name']} ({support['file']}): "
                     + (", ".join(support["called"]) or "loaded"))
    if data["globals"]:
        lines += ["globals:", *("  " + global_line(g, g["here"]) for g in data["globals"])]
    if data["missing"]:
        lines.append("missing (siblings have, this file lacks):")
        lines += [f"  {m['kind']} {m['name']}  in {m['count']}/{m['of']} sibling tests (e.g. {m['example']})"
                  for m in data["missing"][:MAX_MISSING]]
        if len(data["missing"]) > MAX_MISSING:
            lines.append(f"  +{len(data['missing']) - MAX_MISSING} less common")
    return "\n".join(lines)


FIND_SCOPE = "names + notes, not file text"
MAX_FIND_CALLERS, MAX_FIND_TESTS = 5, 4


def format_find(app, words, hits, shown, short_name=str, suggestions=(), extras=None):
    """The find lines. `extras`: unit -> (callers, tests) adds those under each hit (--tests);
    `suggestions`: close names, printed when nothing matched."""
    lines = [f"{app} find {' '.join(words)}: {len(shown)}/{len(hits)} ({FIND_SCOPE})"]
    for h in shown:
        lines.append(f"{unit_text(h['unit'], short_name)}  {h['kind']}  "
                     f"{range_text(h['file'], h['line'], h['end'])}{hint_suffix(h['hint'])}")
        if extras is not None:
            callers, tests = extras[h["unit"]]
            names = [unit_text(u, short_name) for u in callers[:MAX_FIND_CALLERS]]
            more = len(callers) - len(names)
            lines.append("  callers: " + (", ".join(names) + (f" +{more} more" if more else "")
                                          if names else "none"))
            lines.append("  " + (tests_text(tests, short_name, MAX_FIND_TESTS) if tests else "tests: none found"))
    if not hits:
        if suggestions:
            lines.append("close names: " + ", ".join(suggestions))
        lines.append(f"for literal text: find {app} --text <pattern>")
    return "\n".join(lines)


MAX_TEXT_HITS = 40  # full lines of `find --text` unless --all; the rest is one count line per file
TEXT_KINDS = ("src", "test", "doc", "deploy", "script", "config")


def capped_lines(rows, limit, line_of):
    """([full lines of the first `limit` rows], [`file: N hits` per file of the rest, in order]);
    no file is left out."""
    rest = Counter()
    for r in rows[limit:]:
        rest[r["file"]] += 1
    return [line_of(r) for r in rows[:limit]], [f"{f}: {n} hit{'s' if n > 1 else ''}" for f, n in rest.items()]


def format_find_text(app, pattern, rows, show_all=False, short_name=str):
    """`find --text`: one `file:line  unit or heading  [kind]  text` line per match, code first
    (src, then tests, docs, ...); past MAX_TEXT_HITS a `file: N hits` line per remaining file;
    `rows` from views.find_text."""
    if not rows:
        return f"{app} find --text {pattern}: 0 hits"
    ordered = sorted(rows, key=lambda r: TEXT_KINDS.index(r["kind"]))  # stable: file, line order kept

    def line_of(r):
        where = unit_text(r["unit"], short_name) if r["unit"] else r["heading"] or "-"
        return f"{r['file']}:{r['line']}  {where}  [{r['kind']}]  {clip(r['text'], MAX_LINE_TEXT)}"

    full, counts = capped_lines(ordered, len(ordered) if show_all else MAX_TEXT_HITS, line_of)
    lines = [f"{app} find --text {pattern}: {len(full)}/{len(rows)}"] + full + counts
    if counts:
        lines.append("(--all: every line)")
    return "\n".join(lines)


def format_read(app, targets, blocks, lines_of, short_name=str):
    """`read`: per block a `Class#function  file:first-last  — hint` header (`file:line` for a
    place outside functions), then its source lines numbered; `lines_of(file)` = the file's lines."""
    out = [f"{app} read {' '.join(targets)}"]
    for block in blocks:
        head = unit_text(block["unit"], short_name) + "  " if block["unit"] else ""
        out.append(head + range_text(block["file"], block["line"], block["end"]) + hint_suffix(block["hint"]))
        text = lines_of(block["file"])
        numbers = sorted({n for first, last in block["ranges"]
                          for n in range(first, min(last or len(text), len(text)) + 1)})
        width = len(str(numbers[-1])) if numbers else 1
        out += [f"{n:>{width}}  {text[n - 1]}".rstrip() for n in numbers]
        if not numbers and block["ranges"]:
            out.append("  (no lines: file missing or shorter)")
        out += block.get("after", [])  # e.g. a file's definitions as targets
    return "\n".join(out)


def unit_text(unit, short_name):
    """`Class#function`; a NickNamer adds the nicknames."""
    node_id, function = unit
    if function and hasattr(short_name, "function"):
        function = short_name.function(node_id, function)
    return short_name(node_id) + (f"#{function}" if function else "")


def tests_text(found, short_name=str, limit=None, more_hint=""):
    """`tests: a.py, b.py (via User), +n more<more_hint>` for [(test file, via user id)]."""
    shown = found if limit is None else found[:limit]
    texts = [f + (f" (via {short_name(via)})" if via else "") for f, via in shown]
    if len(shown) < len(found):
        texts.append(f"+{len(found) - len(shown)} more{more_hint}")
    return "tests: " + ", ".join(texts)


def nearest_tests(found, limit=MAX_NEAR_TESTS):
    """The tests reaching the unit itself (no `via`), else the first `limit` of the rest."""
    return [t for t in found if not t[1]] or found[:limit]


def format_impact(app, unit, depth, result, short_name=str, show_all=False):
    """unit: (class id, function) or a plain name (a constant, env var, settings key).
    Tests: the nearest ones, then `+N more tests (--all)`, unless `show_all`."""
    levels = result["levels"]
    target = unit if isinstance(unit, str) else unit_text(unit, short_name)
    lines = [f"{app} impact {target} depth {depth}: "
             f"{len(levels[0]) if levels else 0} direct, {sum(map(len, levels))} total"]
    for distance, level in enumerate(levels, 1):
        names = [unit_text(u, short_name) for u in level[:MAX_IMPACT_LINE]]
        more = len(level) - len(names)
        lines.append(f"{distance}: " + ", ".join(names) + (f" +{more} more" if more else ""))
    for kind, label in (("test", "named in tests"), ("doc", "named in docs")):
        places = result.get("mentions", {}).get(kind)
        if places:
            lines.append(f"{label}: {places_text(places, MAX_MENTION_FILES)}")
    tests = result["tests"]
    shown = tests if show_all else nearest_tests(tests)
    lines.append(tests_text(shown, short_name) if tests else "tests: none found")
    if len(shown) < len(tests):
        lines.append(f"+{len(tests) - len(shown)} more tests (--all)")
    if result.get("tours"):
        lines.append(tour_steps_text(result["tours"]))
    return "\n".join(lines)


def tour_steps_text(steps):
    """`tour steps over this code (may go stale): scan-pipeline step 2, ...` for [(tour id, step number)]."""
    return "tour steps over this code (may go stale): " + ", ".join(
        f"{tour_id} step {number}" for tour_id, number in steps)


def e2e_text(e2e):
    """The viewer end-to-end run for a diff that touched viewer code: all checks, or the checks
    naming a changed function (`--only` words repeat; a name's part before its first comma)."""
    only = "".join(f' --only "{only_word(name)}"' for name in e2e["checks"])
    note = "checks naming a changed function; leave out --only to run all" if only else "no check names a changed function"
    return f"e2e: node {E2E_RUNNER}{only}  ({note})"


def format_diff_tests(app, ref, result, short_name=str):
    """`tests --diff`: header, every test file to run (one per line, with their user), one pytest
    command line with all of them when they are Python, the viewer e2e run and the tour steps
    over the changed code when there are any, and the changed units without any test."""
    tests = result["tests"]
    files = [f for f, _ in tests]
    lines = [f"{app} tests --diff {ref}: {len(result['units'])} changed units, {len(files)} test files"]
    lines += [f + (f" (via {short_name(via)})" if via else "") for f, via in tests]
    if files and all(f.endswith(".py") for f in files):
        lines.append("run: pytest " + " ".join(shlex.quote(f) for f in files))
    if result.get("e2e"):
        lines.append(e2e_text(result["e2e"]))
    if result.get("tours"):
        lines.append(tour_steps_text(result["tours"]))
    if result["untested"]:
        names = [unit_text(u, short_name) for u in result["untested"][:MAX_IMPACT_LINE]]
        more = len(result["untested"]) - len(names)
        lines.append("no tests: " + ", ".join(names) + (f" +{more} more" if more else ""))
    if result["broad"]:
        lines.append("many tests reach (`impact` for them): " + ", ".join(
            f"{unit_text(u, short_name)} ({n})" for u, n in result["broad"][:MAX_IMPACT_LINE]))
    return "\n".join(lines)


def format_hotspots(app, days, rows, total, short_name=str):
    """Ranked hotspot rows (views.hotspots), one line each; total = rows before --top."""
    lines = [f"{app} hotspots {days}d: {len(rows)} of {total} (size x commits x untested)"]
    for row in rows:
        lines.append(f"{short_name(row['id'])}  {row['file']}  size {row['size']}  "
                     f"churn {row['churn']}  tested {'yes' if row['tested'] else 'no'}  "
                     f"score {row['score']}")
    return "\n".join(lines)


# --- names index: uses, env, dupes ---

MAX_MENTION_FILES = 8  # files on an impact `named in` line
MAX_USES = 40  # src lines of `uses` unless --all (then a count per file); tests get a third
MAX_ENV_PLACES = 3  # files per read/set/doc part of an `env` line
MAX_DUPES = 30  # literals listed by `dupes` unless --all
MAX_DUPE_FILES = 4  # files on a `dupes` line
MAX_LINE_TEXT = 100  # longest line text printed by `uses`
USES_GROUPS = (("src", ("src",)), ("tests", ("test",)))
USES_COLLAPSED = ("doc", "deploy", "script", "config")  # one count line per kind unless --all
KIND_LABELS = {"doc": "docs", "deploy": "deploy", "script": "scripts", "config": "config"}


def places_text(places, limit=None):
    """`a.py:3,9, b.md:2 +n files` for [(file, line, ...)], files in first-seen order."""
    by_file = {}
    for place in places:
        by_file.setdefault(place[0], []).append(str(place[1]))
    files = list(by_file)
    shown = files if limit is None else files[:limit]
    text = ", ".join(f"{f}:{','.join(by_file[f])}" for f in shown)
    return text + (f" +{len(files) - len(shown)} files" if len(shown) < len(files) else "")


def clip(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + "…"


def collapsed_line(label, found):
    """`docs 3: a.md, b.md +n` for the rows of one kind."""
    files = list(dict.fromkeys(r["file"] for r in found))
    shown = files[:MAX_MENTION_FILES]
    more = f" +{len(files) - len(shown)}" if len(shown) < len(files) else ""
    return f"{label} {len(found)}: " + ", ".join(shown) + more


def format_uses(app, name, rows, show_all=False):
    """`uses`: every line naming `name`: src and tests as lines (code first, then comment / help /
    doc-string lines under `comments / help:`), past the cap a `file: N hits` line per file; docs,
    deploy, scripts and config as one count line per kind (all their lines with --all)."""
    kinds = USES_GROUPS + tuple((KIND_LABELS[k], (k,)) for k in USES_COLLAPSED)
    groups = [(label, [r for r in rows if r["kind"] in kinds_]) for label, kinds_ in kinds]
    counts = ", ".join(f"{label} {len(found)}" for label, found in groups if found)
    lines = [f"{app} uses {name}: {len(rows)}" + (f" ({counts})" if counts else "")]
    for label, found in groups:
        if not found:
            continue
        if label not in KIND_LABELS.values() or show_all:
            ordered = sorted(found, key=lambda r: r["prose"])  # stable: code lines first
            limit = len(ordered) if show_all else MAX_USES if label == "src" else MAX_USES // 3
            full, more = capped_lines(ordered, limit, lambda r: f"  {r['file']}:{r['line']}  {clip(r['text'], MAX_LINE_TEXT)}")
            prose = [i for i, r in enumerate(ordered[:limit]) if r["prose"]]
            if prose and len(prose) < len(full):
                full.insert(prose[0], "  comments / help:")
            lines += [f"{label}:"] + full + [f"  {m}" for m in more]
            if more:
                lines.append("  (--all: every line)")
        else:
            lines.append(collapsed_line(label, found))
    return "\n".join(lines)


def format_env(app, rows):
    """`env`: one line per env var: gaps, then where it is read, set and documented."""
    gaps = Counter(g for r in rows for g in r["gaps"])
    gap_text = ", ".join(f"{n} {g}" for g, n in sorted(gaps.items()))
    lines = [f"{app} env {len(rows)} vars" + (f" (gaps: {gap_text})" if gap_text else "")]
    for r in rows:
        parts = [f"{role} {places_text(r[role], MAX_ENV_PLACES) if r[role] else '-'}"
                 for role in ("read", "set", "doc")]
        flags = f" [{', '.join(r['gaps'])}]" if r["gaps"] else ""
        lines.append(f"{r['name']}{flags}  " + " | ".join(parts))
    return "\n".join(lines)


def format_dupes(app, rows, show_all=False):
    """`dupes`: string literals in 2+ files, most files first."""
    shown = rows if show_all else rows[:MAX_DUPES]
    lines = [f"{app} dupes: {len(shown)}/{len(rows)} literals in 2+ files"]
    lines += [f'"{clip(r["text"], MAX_LINE_TEXT)}" {len(r["places"])}x {r["files"]} files: '
              f'{places_text(r["places"], MAX_DUPE_FILES)}' for r in shown]
    return "\n".join(lines)


def brief_functions_line(data):
    shown = data["functions"][:MAX_BRIEF_FUNCTIONS]
    texts = [f"{r['name']} {r['line']}" + (f"-{r['end']}" if r["end"] > r["line"] else "")
             for r in shown]
    more = len(data["functions"]) - len(shown)
    return ["fns: " + ", ".join(texts) + (f" +{more}" if more else "")] if texts else []


def own_links(view, node_id):
    """The view with only the direct lines that start or end at the node."""
    return {**view, "edges": [e for e in view["edges"]
                              if e["direct"] and node_id in (e["from"], e["to"])]}


def brief_block(item, short_name):
    """One target of a brief: where it is, its notes, warnings, functions, neighbours,
    ripple and tests."""
    view, data = item["view"], item["outline"]
    node_id, function = item["unit"]
    names = labels(view)
    node = next(n for n in view["nodes"] if n["id"] == node_id)
    rows = data["functions"]
    place = (range_text(rows[0]["file"], rows[0]["line"], rows[0]["end"]) if function and rows
             else range_text(data["file"], data["line"], data["end"]))
    head = unit_text(item["unit"], short_name) + f"  {place}  [{node['kind']}] L{node['level']}"
    marks = marks_text(node)
    hint = rows[0]["hint"] if function and rows else node.get("hint")
    lines = [head + (f" {marks}" if marks else "") + (f": {hint}" if hint else "")]
    lines += rules_lines("rules", node.get("rules"))
    if node.get("warnings"):
        lines.append("warn: " + ", ".join(w["text"] for w in node["warnings"]))
    if function:
        lines += function_lines(view, node_id, names, short_name, only=function)
    else:
        lines += brief_functions_line(data)
        neighbours = edge_lines(own_links(view, node_id), short_name=short_name, faded=False)
        lines += neighbours[:MAX_BRIEF_NEIGHBOURS]
        if len(neighbours) > MAX_BRIEF_NEIGHBOURS:
            lines.append(f"+{len(neighbours) - MAX_BRIEF_NEIGHBOURS} more links")
    levels = item["impact"]["levels"]
    if levels:
        lines.append(f"ripple: {len(levels[0])} direct, {sum(map(len, levels))} within depth "
                     f"{len(levels)}")
    if node.get("tested_by"):
        lines.append(tests_text([(f, "") for f in node["tested_by"]], limit=MAX_BRIEF_TESTS,
                                more_hint=TESTS_HINT))
    return lines


def format_brief(app, items, short_name=str):
    """A context block to paste into another agent's brief."""
    lines = [f"{app} brief: " + ", ".join(unit_text(i["unit"], short_name) for i in items)]
    for item in items:
        lines += brief_block(item, short_name)
    return "\n".join(lines)


# --- tours (DESIGN 7c): the lean step text, the list, and the Markdown export ---

def author_text(item):
    return f"[{item['by']}{' ai' if item['ai'] else ''}]"


def format_tours(app, rows):
    """`scan-pipeline How a scan works 7 steps [you] 2 ai, 1 changed`, one per tour; a Start-here
    tour adds `start tour`."""
    lines = []
    for row in rows:
        extra = ["start tour" if row.get("start") else "",
                 f"{row['ai_steps']} ai" if row["ai_steps"] and not row["ai"] else "",
                 f"{row['changed']} changed" if row["changed"] else ""]
        extra = ", ".join(e for e in extra if e)
        lines.append(f"{row['id']} {row['title']} {row['steps']} steps {author_text(row)}"
                     + (f" {extra}" if extra else ""))
    return "\n".join(lines) or f"{app}: no tours found"


def code_line(number, text, marked, width=4):
    return f"{'>' if marked else ' '}{str(number).rjust(width)} {text}"


def excerpt_lines(code, marked_only=False):
    """Today's excerpt with line numbers and `>` on marked lines (`path:line text` with
    marked_only)."""
    if not code or code["from"] is None:
        return []
    rows = list(enumerate(code["lines"], code["from"]))
    if marked_only:
        return [f"{code['path']}:{n} {text}" for n, text in rows if n in code["mark"]]
    return [f"{code['path']}:{code['from']}-{code['to']}",
            *(code_line(n, text, n in code["mark"]) for n, text in rows)]


def step_audio_text(step):
    rec = step["audio"]["rec"]
    return " audio: " + ("voice" if not rec else "rec (stale)" if rec["stale"] else "rec")


def open_comments_text(count):
    """` (2 open comments)` after a step or tour that has open comment threads (DESIGN 7d)."""
    return f" ({count} open comment{'s' if count != 1 else ''})" if count else ""


def step_head(step, label, audio=False):
    lost = f" (lost: {', '.join(step['lost_targets'] or [step['code']['at']])})" \
        if step["lost"] else ""
    return (f"{label} {step['title']} {{{', '.join(step['targets'])}}}"
            + (" (changed)" if step["stale"] else "") + lost
            + open_comments_text(step.get("comments", 0))
            + (step_audio_text(step) if audio else ""))


def step_view_command(app, step):
    """The `view` command that shows the step's graph (targets as written, lost ones left out)."""
    targets = [t for t in step["targets"] if t not in step["lost_targets"]]
    return view_command(app, step["level"], focus=targets, expand=step["expand"])


def view_text_of(view):
    """`lines=all selfcalls=false hide_kind=signal,dependency`: a tour's or step's view options
    (`tour --view` takes the same pairs)."""
    def value(v):
        return ",".join(map(value, v)) if isinstance(v, list) else str(v).lower() if isinstance(v, bool) else str(v)
    return " ".join(f"{k}={value(v)}" for k, v in (view or {}).items())


def step_lines(step, label, code=True, marked=False, audio=False):
    lines = [step_head(step, label, audio)]
    if step.get("view"):
        lines.append(f"view (this step): {view_text_of(step['view'])}")
    if not marked:
        lines.append(step["text"])
    if code:
        lines += excerpt_lines(step["code"], marked_only=marked)
        lines += [note_line(n) for n in step.get("notes") or []]
    return lines


def note_line(note):
    """`note 50-51: text (at "cue words")`: a step's line note (`tour --note` writes them)."""
    where = "?" if note["from"] is None else str(note["from"]) if note["from"] == note["to"] \
        else f"{note['from']}-{note['to']}"
    return f"note {where}: {note['text']}" + (f' (at "{note["cue"]}")' if note.get("cue") else "")


def format_tour(app, tour, number=None, code=True, marked=False, audio=False):
    """The lean tour text (DESIGN 7c): header, then per step `N. Title {targets}`, the text and
    the step's own excerpt lines. `number`: that step only, its `view` command first."""
    steps = tour["steps"]
    if number is not None:
        if not 1 <= number <= len(steps):
            raise ValueError(f"{tour['id']} has steps 1-{len(steps)}, not {number}")
        step = steps[number - 1]
        tour_view = [f"view (whole tour): {view_text_of(tour['view'])}"] if tour.get("view") else []
        return "\n".join([step_view_command(app, step), *tour_view,
                          *step_lines(step, f"{number}/{len(steps)}", code, marked, audio)])
    changed = sum(s["stale"] for s in steps)
    lines = [f"{app} tour {tour['id']} {tour['title']} {len(steps)} steps {author_text(tour)}"
             + (f", {changed} changed" if changed else "")
             + open_comments_text(tour.get("comments", 0))]
    if tour.get("view"):
        lines.append(f"view (whole tour): {view_text_of(tour['view'])}")
    for n, step in enumerate(steps, 1):
        lines += step_lines(step, f"{n}.", code, marked, audio)
    return "\n".join(lines)


def tour_markdown(app, tour):
    """The tour as a readable Markdown document (`tour --md`, GET /api/tours/<id>.md)."""
    steps = tour["steps"]
    out = [f"# {tour['title']}", "",
           f"By {'AI' if tour['ai'] else tour['by']} · {len(steps)} steps · exported from "
           f"Coderstanding (`coderstanding tour {app} {tour['id']} --md`)"]
    for n, step in enumerate(steps, 1):
        out += ["", f"## {n}. {step['title']}", "", plain_links(step["text"], "`{}`")]
        code = step["code"]
        if step["stale"]:
            out += ["", "> Note: the code changed since this step was written"
                    + (f" (it was lines {code['was']})." if code and code["was"] else ".")]
        if code and code["from"] is not None:
            out += ["", f"`{code['path']}:{code['from']}-{code['to']}`", "", "```"]
            out += [f"{'>' if no in code['mark'] else ' '} {str(no).rjust(4)}  {text}"
                    for no, text in enumerate(code["lines"], code["from"])]
            out.append("```")
            for note in step.get("notes") or []:
                out += ["", f"> {note_line(note)}"]
        elif code:
            out += ["", f"> Note: the code this step showed (`{code['at']}`) is gone."]
    return "\n".join(out) + "\n"


def comment_where(comment, step_labels):
    """`step 2 Hits m.py:15` / `tour` / `lost step k9`: where a thread's first comment sits."""
    part = comment["target"].partition("#")[2]
    where = "tour" if not part else step_labels.get(part) or f"lost step {part}"
    if comment["anchor"]:
        where += f" {comment['path']}:{comment['line']}" if comment["line"] else \
            f" line in {comment['anchor']['at']} (code gone)"
    if comment["quote"]:
        where += f' "{comment["quote"]}"'
    return where


def format_comments(app, tour, comments, show_resolved=False):
    """`tour <app> <id> --comments [--all]` (DESIGN 7d): one line per comment, replies indented,
    each with its id for `comment --reply/--resolve`; a thread's first line says where it is.
    Resolved threads are counted in the header and listed only with show_resolved."""
    labels = {s["key"]: f"step {n} {s['title']}" for n, s in enumerate(tour["steps"], 1)}
    threads = {c["thread"] for c in comments}
    resolved = len({c["thread"] for c in comments if c["resolved"]})
    lines = [f"{app} tour {tour['id']} comments: {len(threads) - resolved} open"
             + (f", {resolved} resolved" if resolved else "")
             + (" (--all lists them)" if resolved and not show_resolved else "")]
    for c in comments if show_resolved else [c for c in comments if not c["resolved"]]:
        author = c["by"] + (" (ai)" if c["ai"] else "")
        text = "(deleted)" if c["deleted"] else c["text"]
        if c["parent"]:
            head = f"  [{c['id']}] {author}:"
        else:
            state = f" (resolved by {c['resolved']['by']})" if c["resolved"] else ""
            head = f"[{c['id']}] {comment_where(c, labels)}{state} {author}:"
        indent = "      " if c["parent"] else "    "
        lines.append(f"{head} {text}".replace("\n", "\n" + indent))
    return "\n".join(lines)


def tour_warning_lines(stale, lost, lost_comments=None):
    """`stale tour steps: scan-pipeline 4, 6` / `lost tour steps: ...` / `lost tour comments:
    tour:walk#k9 2` (comments on a deleted step) for `warnings`."""
    def grouped(items):
        tours = {}
        for tour_id, number in items:
            tours.setdefault(tour_id, []).append(str(number))
        return "; ".join(f"{t} {', '.join(ns)}" for t, ns in tours.items())
    return ([f"stale tour steps (code changed since written): {grouped(stale)}"] if stale else []) + \
        ([f"lost tour steps: {grouped(lost)}"] if lost else []) + \
        ([f"lost tour comments: {'; '.join(f'{t} {n}' for t, n in lost_comments.items())}"]
         if lost_comments else [])



# --- cards (DESIGN 7, 7d): specs and memos; the tree, hits with their paths, one card ---

def card_facts(card, with_author=False):
    """`L2 implemented, p:high, i:low, 3 links, ai` (the bracket after a card's title)."""
    facts = [f"L{card['level']}", card["stage"],
             f"p:{card['priority']}" if card["priority"] else "",
             f"i:{card['importance']}" if card["importance"] else "",
             f"{len(card['links'])} links" if card["links"] else "",
             ("ai" if not with_author else f"ai: {', '.join(card['ai_fields'])}") if card["ai"] else "",
             f"{len(card['problems'])} problems" if card["problems"] and not with_author else ""]
    if with_author:
        facts.insert(0, f"by {card['by']}")
    return ", ".join(f for f in facts if f)


def hit_facts(hit):
    return ", ".join(f for f in (hit["stage"], hit["priority"] and f"p:{hit['priority']}",
                                 hit["importance"] and f"i:{hit['importance']}") if f)


def card_tree_lines(data, boxes, depth=0):
    lines = []
    for box in boxes:
        card = data["items"][box["id"]]
        finer = f" +{box['finer']} finer" if box["finer"] else ""
        lines.append(f"{'  ' * depth}{card['id']}: {card['title']} [{card_facts(card)}]{finer}")
        lines += card_tree_lines(data, box["children"], depth + 1)
    return lines


def format_cards(app, data):
    """`coderstanding specs|memos <app> [query]`: the tree (or the hits with their paths), problems."""
    items, folder = data["items"], data["folder"]
    if data["hits"] is not None:
        lines = [f"{h['id']}: {h['path']} [{hit_facts(h)}]" for h in data["hits"]]
        lines = lines or [f"{app}: no {data['kind']} matches"]
    else:
        lines = [f"{app} {folder}: {len(items)}, levels 1-{data['max_level']}"] if items else \
            [f"{app}: no {folder} yet"]
        lines += card_tree_lines(data, data["tree"])
    lines += [f"problem: {p['id']}: {p['problem']}" for p in data["problems"]]
    return "\n".join(lines)


def format_card(app, card):
    """`coderstanding spec|memo <app> <id>`: head, path, children, links, problems, then the text."""
    lines = [f"{app} {card['kind']} {card['id']}: {card['title']} [{card_facts(card, with_author=True)}]",
             "path: " + " › ".join(p["title"] for p in card["path"])]
    if card["children"]:
        lines.append(f"children: {', '.join(card['children'])}")
    if card["links"]:
        lines.append(f"links: {', '.join(card['links'])}")
    lines += [f"problem: {p}" for p in card["problems"]]
    if card["text"]:
        lines += ["", card["text"]]
    return "\n".join(lines)


def linked_lines(hits):
    """`specs: effects [implemented], ...` / `memos: ...` for a class or line (`node`)."""
    by_kind = {}
    for hit in hits:
        by_kind.setdefault(hit["kind"], []).append(f"{hit['id']} [{hit_facts(hit)}]")
    return [f"{kind}s: {', '.join(rows)}" for kind, rows in by_kind.items()]


# --- planned work (`plan <app>`, DESIGN 7f) ---

def plan_row_text(row, name):
    """One planned or manual item: `name [planned] [todo] work:high: what it is for`."""
    where = f" (where: {row.get('where') or 'unknown'})" if row["made"] == MANUAL else ""
    marks = " ".join(t for t in (f"[{row['made']}]", plan_mark(row)) if t)
    return f"  {name} {marks}{where}{hint_suffix(row.get('hint'))}"


def match_lines(row, target, real_id=""):
    """`built now?` / `close:` lines under a planned item, with the link command."""
    matches = row.get("matches") or {}
    lines = [f"    built now? real {real_id}{m}: plan --link {target} {real_id}{m}" for m in matches.get("exact", [])]
    lines += [f"    close: {real_id}{m} (if it is this one: plan --link {target} {real_id}{m})"
              for m in matches.get("close", [])]
    return lines


def format_plan(app, rows):
    """`plan <app>`: planned and manual classes, planned functions, hand-made lines, stubs."""
    classes = [r for r in rows["classes"] if not r["real"]]
    counts = {"planned": sum(r["made"] == PLANNED for r in classes), "manual": sum(r["made"] == MANUAL for r in classes),
              "functions": sum(not r["built"] for r in rows["functions"]), "stubs": len(rows["stubs"])}
    lines = [f"{app} plan:{plan_text(counts)[len(' plan:'):] or ' nothing planned, no stubs'}"]
    if classes:
        lines.append("classes (not in the scanned code):")
        for row in classes:
            lines.append(plan_row_text(row, row["id"]))
            if row["made"] == PLANNED:
                lines += match_lines(row, row["id"])
    if rows["functions"]:
        lines.append("functions:")
        for row in rows["functions"]:
            target = f"{row['owner']}#{row['name']}"
            state = " built now (linked)" if row["built"] else " (stub in the code)" if row["stub"] else ""
            lines.append(f"  {target} [planned {row['stage']}]{state}{hint_suffix(row.get('hint'))}")
            if not row["built"] and not row["stub"]:
                lines += match_lines(row, target, f"{row['owner']}#")
    if rows["lines"]:
        lines.append("lines:")
        lines += [f"  {r['from']} {'..>' if r['made'] == PLANNED else '>'} {r['to']} [{r['made']}]"
                  + (" (in the code now)" if r["real"] else "") for r in rows["lines"]]
    if rows["stubs"]:
        lines.append("stubs (no real body yet):")
        lines += [f"  {r['owner']}#{r['name']} [stub] {r['file']}:{r['line']}{hint_suffix(r.get('hint'))}"
                  for r in rows["stubs"]]
    return "\n".join(lines)


# --- progress tracks (`progress <app>`, progress.py) ---

MAX_TODO = 40  # not-done items listed before `+N more (--all)`


def delta_text(delta):
    if delta is None:
        return ""
    return " · no change since last scan" if delta == 0 else f" · {delta:+d} since last scan"


def track_line(row, width):
    """`Proven    26.9% (67/249) · +3 since last scan   functions in game/** · done: tag proven`."""
    numbers = f"{progress_numbers(row)} ({row['done']}/{row['total']})"
    return f"{row['name']:<{width}} {numbers}{delta_text(row.get('delta'))}   {row['of']} · done: {row['done_rule']}"


def progress_numbers(row):
    return "-" if row["percent"] is None else f"{row['percent']:.1f}%"


def format_progress(app, summary, problems=()):
    """`progress <app>`: one line per track, then any bad track settings."""
    width = max((len(r["name"]) for r in summary), default=0)
    lines = [f"{app} progress (own tracks: settings.json \"tracks\"; --track NAME: per domain and what is left)"]
    lines += [track_line(row, width) for row in summary]
    return "\n".join(lines + [f"note: {p}" for p in problems])


def format_track(app, row, domains, todo, show_all=False, trend=()):
    """`progress <app> --track NAME`: the track, a line per domain, the trend, the items not done."""
    lines = [f"{app} {track_line(row, len(row['name']))}"]
    if domains:
        width = max(len(d["domain"] or "(no domain)") for d in domains)
        lines.append("domains:")
        lines += [f"  {(d['domain'] or '(no domain)'):<{width}} {progress_numbers(d)} ({d['done']}/{d['total']})"
                  for d in domains]
    if len(trend) > 1:
        lines.append("trend: " + " → ".join(progress_numbers(t) for t in trend[-8:])
                     + f" (last {min(len(trend), 8)} scans)")
    if todo:
        shown = todo if show_all else todo[:MAX_TODO]
        more = len(todo) - len(shown)
        lines.append(f"not done ({len(todo)}):")
        lines += [f"  {target}" for target in shown]
        if more:
            lines.append(f"  +{more} more (--all)")
    return "\n".join(lines)
