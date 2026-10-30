"""Slim text output (docs/DESIGN.md 11), built on the view engine's `compute_view` dict.

Terse on purpose: an AI reads it instead of source files to save tokens.
"""

import shlex

from .checks import STALE_KIND, layer_label
from .items import plain_links
from .notes import ARROW, FUNCTION_SEP
from .views import compute_view

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
    return name + marks


def unique_names(nodes, name_of):
    counts = {}
    for node in nodes:
        counts[name_of(node)] = counts.get(name_of(node), 0) + 1
    return {n["id"]: name_of(n) for n in nodes if counts[name_of(n)] == 1}


def labels(view):
    """id -> printed name: the short name, else the file name, else the id where names clash."""
    nodes = view["nodes"]
    names = {n["id"]: n["id"] for n in nodes}
    names.update(unique_names(nodes, lambda n: n["file"].rsplit("/", 1)[-1] or n["id"]))
    names.update(unique_names(nodes, lambda n: n["name"]))
    return {n["id"]: node_label(n, names[n["id"]]) for n in nodes}


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
    edges = sorted((e for e in view["edges"] if not e["faded"]),
                   key=lambda e: (order[e["from"]], order[e["to"]]))
    lines = []
    for edge in edges:
        lines.append(edge_line(edge, names, all_calls, short_name))
        if descriptions and edge.get("description"):
            lines.append(description_line(edge))
        if descriptions:
            lines += review_line(edge)
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


def format_graph(app, view, all_calls=False, short_name=str, graph=None):
    counts = view["counts"]
    header = f"{app} L{view['level']} {view['view']} {counts['shown']}/{counts['total']}"
    header += hubs_text(graph, short_name) if graph else ""
    return "\n".join([header, *edge_lines(view, all_calls, short_name),
                      *alone_line(view, labels(view))])


def rules_lines(label, rules):
    """`rules X: first line`, further lines indented; nothing when there are no rules."""
    lines = [line for line in (rules or "").splitlines() if line.strip()]
    if not lines:
        return []
    return [f"{label}: {lines[0]}", *("  " + line for line in lines[1:])]


def format_zoom(app, view, all_calls=False, short_name=str):
    zoom, names = view["zoom"], labels(view)
    header = f"{app} zoom {names[zoom['from']]}>{names[zoom['to']]} L{view['level']} {view['view']}"
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


def node_detail(node):
    """A class: kind, file:line, level, domain, size, status/tags, hint; then warnings,
    tests, rules and description."""
    parts = [f"[{node['kind']}]", f"{node['file']}:{node['line']}", f"L{node['level']}"]
    if node.get("hub_degree"):
        parts.append(hub_mark(node["hub_degree"]).strip())
    if node.get("domain"):
        parts.append(f"domain={node['domain']}")
    parts += [text for text in (size_text(node), marks_text(node)) if text]
    lines = [" ".join(parts) + (f": {node['hint']}{stale_mark(node, 'hint')}" if node.get("hint") else "")]
    if node.get("warnings"):
        lines.append("warn: " + ", ".join(w["text"] for w in node["warnings"]))
    if node.get("ignored_warnings"):
        lines.append("ignored: " + ", ".join(f"{w['text']} ({w['reason']})"
                                             for w in node["ignored_warnings"]))
    if node.get("tested_by"):
        lines.append("tests: " + ", ".join(node["tested_by"][:MAX_TESTS_SHOWN])
                     + (f" +{len(node['tested_by']) - MAX_TESTS_SHOWN} more"
                        if len(node["tested_by"]) > MAX_TESTS_SHOWN else ""))
    lines += rules_lines("rules" + stale_mark(node, "rules"), node.get("rules"))
    lines += review_line(node, "")
    description = node.get("description")
    return lines + ([description + stale_mark(node, "description")] if description else [])


def node_head(app, view, node_id, names, suffix=""):
    node = next(n for n in view["nodes"] if n["id"] == node_id)
    return [f"{app} node {names[node_id]} L{view['level']} {view['view']}{suffix}",
            *node_detail(node)]


def format_node(app, view, node_id, all_calls=False, short_name=str):
    return "\n".join([*node_head(app, view, node_id, labels(view)),
                      *edge_lines(view, all_calls, short_name)])


# --- functions of expanded classes (DESIGN 12c) ---

def counted_text(name, count):
    return name + (f" ×{count}" if count > 1 else "")


def end_text(names, node_id, fn):
    return f"{names[node_id]}#{fn}" if fn else names[node_id]


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
        head = f"{names[node_id]}#{name}"
        lines += [f"{head} {arrow} {text}" for arrow, text in grouped_out(out, names, short_name)]
        if callers:  # under its call lines, else on the function's own line
            lines.append(("  " if out else head + " ") + "in: " + callers_text(callers, names))
        if not out and not callers:
            quiet.append(name)
    if only is None:
        for link in fan:
            if link["from"] == node_id and not link["from_fn"] and not link["internal"]:
                called = ", ".join(counted_text(m["name"], m["count"]) for m in link["members"])
                target = end_text(names, link["to"], link["to_fn"])
                lines.append(f"{names[node_id]} (fields) {'>' if link['direct'] else '~>'} "
                             f"{target}: [{', '.join(link['kinds'])}] {called}".rstrip())
        if quiet:
            more = len(quiet) - QUIET_FUNCTIONS
            lines.append("no calls: " + ", ".join(quiet[:QUIET_FUNCTIONS])
                         + (f" +{more}" if more > 0 else ""))
    return lines


def format_functions(app, view, node_id, short_name=str):
    """`node --functions`: the class, then its function lines."""
    names = labels(view)
    return "\n".join([*node_head(app, view, node_id, names, " functions"),
                      *function_lines(view, node_id, names, short_name)])


# --- the whole screen as text (`coderstanding view`, the viewer's Copy for AI) ---

def view_command(app, level=1, concrete=False, show=None, zoom=None, expand=(), select=None,
                 focus=()):
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
    if focus:
        parts += ["--focus", *focus]
    for node_id in expand:
        parts += ["--expand", node_id]
    if select:
        parts += ["--select", select]
    flags = [("--tests", show.get("test")), ("--no-scenes", show.get("scene") is False),
             ("--no-external", show.get("external") is False)]
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
    for node_id in view["expanded"]:
        lines += function_lines(view, node_id, names, short_name)
    if select:
        lines += selection_lines(view, select, names, short_name)
    return "\n".join(lines)


def short_namer(graph):
    return lambda i: graph.nodes[i].name if i in graph.nodes else i.split(":", 1)[-1]


def view_text(app, graph, notes, *, level=1, view="all", show=None, zoom=None, expand=(),
              select=None, focus=()):
    """What `coderstanding view` prints and /api/text returns: one place for both."""
    shown = compute_view(graph, notes, level=level, view=view, show=show, zoom=zoom,
                         expand=expand, focus=focus)
    command = view_command(app, level, view == "concrete", show, zoom, expand, select, focus)
    return format_view(app, shown, command, select, short_namer(graph))


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


# --- reading commands: outline, find, impact, brief (DESIGN 11) ---

MAX_BRIEF_NEIGHBOURS = 5
MAX_BRIEF_FUNCTIONS = 6
MAX_BRIEF_TESTS = 6
MAX_IMPACT_LINE = 40


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


def function_text(row):
    """`name -> Type  file:first-last  — hint`."""
    name = row["name"] + (f" -> {row['type']}" if row["type"] else "")
    return f"  {name}  {range_text(row['file'], row['line'], row['end'])}{hint_suffix(row['hint'])}"


def format_outline(app, data, function=None):
    lines = [f"{app} outline {data['name']}" + (f"#{function}" if function else ""),
             outline_head(data)]
    if data["functions"]:
        lines += ["functions:" if not function else "function:",
                  *(function_text(row) for row in data["functions"])]
    if data["fields"]:
        lines += ["fields:", *(f"  {f['name']}{': ' + f['type'] if f['type'] else ''}  "
                               f"{range_text(f['file'], f['line'])}" for f in data["fields"])]
    if data["inner"]:
        lines += ["inside:", *(f"  {n['name']} [{n['kind']}]  "
                               f"{range_text(n['file'], n['line'], n['end'])}"
                               for n in data["inner"])]
    return "\n".join(lines)


def format_find(app, words, hits, shown, short_name=str):
    lines = [f"{app} find {' '.join(words)}: {len(shown)}/{len(hits)}"]
    lines += [f"{unit_text(h['unit'], short_name)}  {h['kind']}  "
              f"{range_text(h['file'], h['line'], h['end'])}{hint_suffix(h['hint'])}" for h in shown]
    return "\n".join(lines)


def unit_text(unit, short_name):
    node_id, function = unit
    return short_name(node_id) + (f"#{function}" if function else "")


def tests_text(found, short_name=str, limit=None):
    """`tests: a.py, b.py (via User), +n more` for [(test file, via user id)]."""
    shown = found if limit is None else found[:limit]
    texts = [f + (f" (via {short_name(via)})" if via else "") for f, via in shown]
    if len(shown) < len(found):
        texts.append(f"+{len(found) - len(shown)} more")
    return "tests: " + ", ".join(texts)


def format_impact(app, unit, depth, result, short_name=str):
    levels = result["levels"]
    lines = [f"{app} impact {unit_text(unit, short_name)} depth {depth}: "
             f"{len(levels[0]) if levels else 0} direct, {sum(map(len, levels))} total"]
    for distance, level in enumerate(levels, 1):
        names = [unit_text(u, short_name) for u in level[:MAX_IMPACT_LINE]]
        more = len(level) - len(names)
        lines.append(f"{distance}: " + ", ".join(names) + (f" +{more} more" if more else ""))
    lines.append(tests_text(result["tests"], short_name) if result["tests"] else "tests: none found")
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
        lines.append(tests_text([(f, "") for f in node["tested_by"]], limit=MAX_BRIEF_TESTS))
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
    """`scan-pipeline How a scan works 7 steps [you] 2 ai, 1 changed`, one per tour."""
    lines = []
    for row in rows:
        extra = [f"{row['ai_steps']} ai" if row["ai_steps"] and not row["ai"] else "",
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


def step_head(step, label, audio=False):
    lost = f" (lost: {', '.join(step['lost_targets'] or [step['code']['at']])})" \
        if step["lost"] else ""
    return (f"{label} {step['title']} {{{', '.join(step['targets'])}}}"
            + (" (changed)" if step["stale"] else "") + lost
            + (step_audio_text(step) if audio else ""))


def step_view_command(app, step):
    """The `view` command that shows the step's graph (targets as written, lost ones left out)."""
    targets = [t for t in step["targets"] if t not in step["lost_targets"]]
    return view_command(app, step["level"], focus=targets, expand=step["expand"])


def step_lines(step, label, code=True, marked=False, audio=False):
    lines = [step_head(step, label, audio)]
    if not marked:
        lines.append(step["text"])
    if code:
        lines += excerpt_lines(step["code"], marked_only=marked)
    return lines


def format_tour(app, tour, number=None, code=True, marked=False, audio=False):
    """The lean tour text (DESIGN 7c): header, then per step `N. Title {targets}`, the text and
    the step's own excerpt lines. `number`: that step only, its `view` command first."""
    steps = tour["steps"]
    if number is not None:
        if not 1 <= number <= len(steps):
            raise ValueError(f"{tour['id']} has steps 1-{len(steps)}, not {number}")
        step = steps[number - 1]
        return "\n".join([step_view_command(app, step),
                          *step_lines(step, f"{number}/{len(steps)}", code, marked, audio)])
    changed = sum(s["stale"] for s in steps)
    lines = [f"{app} tour {tour['id']} {tour['title']} {len(steps)} steps {author_text(tour)}"
             + (f", {changed} changed" if changed else "")]
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
        elif code:
            out += ["", f"> Note: the code this step showed (`{code['at']}`) is gone."]
    return "\n".join(out) + "\n"


def tour_warning_lines(stale, lost):
    """`stale tour steps: scan-pipeline 4, 6` / `lost tour steps: ...` for `warnings`."""
    def grouped(items):
        tours = {}
        for tour_id, number in items:
            tours.setdefault(tour_id, []).append(str(number))
        return "; ".join(f"{t} {', '.join(ns)}" for t, ns in tours.items())
    return ([f"stale tour steps (code changed since written): {grouped(stale)}"] if stale else []) + \
        ([f"lost tour steps: {grouped(lost)}"] if lost else [])

