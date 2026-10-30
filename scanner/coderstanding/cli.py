"""Command line: `coderstanding <command> ...`. Add a command with one add_* function."""

import argparse
import shlex
import sys
from collections import Counter
from pathlib import Path

from . import textout
from .textout import short_namer
from .apps import (AppError, SETTINGS, find_app, find_id, load_graph, parse_target, register,
                   remove_app, rename_app, rescan, selection_id)
from .graph_json import write_graph
from .levels import parse_hub_threshold
from .checks import RULE_FILTERS, warnings_by_kind
from .notes import (AI_AUTHOR, clean_value, edge_key, function_key, is_function,
                    node_key)
from .scan import default_output, scan_repo
from .testcode import is_test, files_to_retest
from .tours import open_tours
from .views import (CONCRETE, EXPAND_ALL, IMPACT_DEPTH, brief, compute_view, effective_level, find,
                    impact, outline)

CURATED_FIELDS = ("level", "domain", "hint")
TOP_CALLS = 3


def run_scan(args):
    root = Path(args.path)
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")
    out = Path(args.output) if args.output else default_output(root)
    cache_dir = out.parent / "cache" if args.output else None  # -o keeps the scanned repo untouched
    graph = scan_repo(root, use_cache=not args.no_cache, cache_dir=cache_dir, **scan_settings(args))
    write_graph(graph, out)
    print(f"{len(graph.nodes)} nodes, {len(graph.edges)} edges -> {out}")


def add_scan(commands):
    parser = commands.add_parser("scan", help="scan a repo and write graph.json")
    parser.add_argument("path", help="repo folder to scan")
    parser.add_argument("-o", "--output", help="graph.json path "
                        "(default: <path>/.coderstanding/graph.json); its cache goes next to it")
    parser.add_argument("--no-cache", action="store_true",
                        help="parse every file; do not read or write the per-file cache")
    hub_threshold_option(parser)
    parser.set_defaults(run=run_scan)


def hub_threshold_arg(text):
    try:
        return parse_hub_threshold(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def hub_threshold_option(parser):
    parser.add_argument("--hub-threshold", type=hub_threshold_arg, metavar="N|auto|off",
                        help="classes with at least N distinct neighbours are level 1 "
                        "(auto = the god-class threshold, off/0 = none)")


def scan_settings(args):
    """The scan settings given on the command line (only those given)."""
    return {} if args.hub_threshold is None else {"hub_threshold": args.hub_threshold}


def run_add(args):
    entry = register(args.name, args.path, args.data)
    if args.hub_threshold is not None:
        find_app(args.name).change_settings(scan_settings(args))
    print(f"{args.name} -> {entry['path']}")


def add_add(commands):
    parser = commands.add_parser("add", help="register an app (repo) by name")
    parser.add_argument("name")
    parser.add_argument("path", help="repo folder")
    parser.add_argument("--data", help="folder for graph.json/notes.jsonl "
                        "(default: <path>/.coderstanding)")
    hub_threshold_option(parser)
    parser.set_defaults(run=run_add)


def run_settings(args):
    app = find_app(args.app, args.data)
    changes = {}
    for token in args.items:
        name, sep, text = token.partition("=")
        if not sep:
            raise AppError(f"not key=value: {token}")
        changes[name] = text
    settings = app.change_settings(changes) if changes else app.scan_settings()
    print(f"{app.name} settings " + " ".join(f"{k}={'off' if v == 0 else v}"
                                             for k, v in settings.items()))


def add_settings(commands):
    parser = commands.add_parser("settings", help="show or change scan settings "
                                 f"({', '.join(SETTINGS)}); a change rescans on the next command")
    app_args(parser, view_options=False)
    parser.add_argument("items", nargs="*", help="key=value ...")
    parser.set_defaults(run=run_settings)


# --- shared by the text commands ---

def app_args(parser, view_options=True, optional=False):
    parser.add_argument("app", nargs="?" if optional else None, help="registered name or repo folder")
    parser.add_argument("--data", help="folder holding graph.json/notes.jsonl")
    if view_options:
        parser.add_argument("--concrete", action="store_true", help="concrete view")
        parser.add_argument("--calls", action="store_true", help="list every call")
        parser.add_argument("--no-scenes", action="store_true", help="hide scenes")
        parser.add_argument("--no-external", action="store_true", help="hide outside code")
        parser.add_argument("--tests", action="store_true", help="show test code (hidden by default)")


def open_app(args):
    """(app, graph, notes), rescanning if stale; warns about lost notes."""
    app = find_app(args.app, args.data)
    graph, notes = load_graph(app), app.load_notes()
    app.checked(graph, notes)
    lost = notes.lost(graph)
    if lost:
        print(f"warning: {len(lost)} lost notes: {', '.join(lost[:5])}", file=sys.stderr)
    return app, graph, notes


def view_options(args):
    return {"view": CONCRETE if args.concrete else "all",
            "show": {"scene": not args.no_scenes, "external": not args.no_external,
                     "test": args.tests}}


def unique_namer(graph):
    """Short name where it is unique in the graph, else the full id (lists across the repo)."""
    counts = Counter(n.name for n in graph.nodes.values())
    return lambda i: graph.nodes[i].name if counts[graph.nodes[i].name] == 1 else i


def run_graph(args):
    app, graph, notes = open_app(args)
    view = compute_view(graph, notes, level=args.level, **view_options(args))
    print(textout.format_graph(app.name, view, args.calls, short_namer(graph), graph))


def add_graph(commands):
    parser = commands.add_parser("graph", help="print the graph at a level as slim text")
    app_args(parser)
    parser.add_argument("level", nargs="?", type=int, default=1, help="0 = domains")
    parser.set_defaults(run=run_graph)


def add_via(zoom, graph, notes, args):
    """Set zoom["via"] to the --via classes; unset it so the engine finds the path."""
    if args.via:
        zoom["via"] = [find_id(graph, notes, n) for n in args.via]


def add_via_arg(parser):
    parser.add_argument("--via", action="append", default=[], metavar="NAME",
                        help="hidden class the zoomed line runs through (repeat)")


def run_zoom(args):
    app, graph, notes = open_app(args)
    zoom = {"from": find_id(graph, notes, args.from_name),
            "to": find_id(graph, notes, args.to_name), "level": args.level}
    add_via(zoom, graph, notes, args)
    view = compute_view(graph, notes, zoom=zoom, **view_options(args))
    print(textout.format_zoom(app.name, view, args.calls, short_namer(graph)))


def add_zoom(commands):
    parser = commands.add_parser("zoom", help="drill into one connection A -> B")
    app_args(parser)
    parser.add_argument("from_name", metavar="from")
    parser.add_argument("to_name", metavar="to")
    parser.add_argument("--level", type=int, default=1, help="level the line is seen at")
    add_via_arg(parser)
    parser.set_defaults(run=run_zoom)


def run_node(args):
    app, graph, notes = open_app(args)
    node_id = find_id(graph, notes, args.name)
    level = args.level
    if level is None:
        level = effective_level(graph, notes, node_id) if node_id in graph.nodes else 0
    zoom = {"from": node_id, "to": None, "level": level}
    if args.functions:  # every shown class open, so callers show their functions too
        view = compute_view(graph, notes, zoom=zoom, expand=[EXPAND_ALL], **view_options(args))
        print(textout.format_functions(app.name, view, node_id, short_namer(graph)))
        return
    view = compute_view(graph, notes, zoom=zoom, **view_options(args))
    print(textout.format_node(app.name, view, node_id, args.calls, short_namer(graph)))


def add_node(commands):
    parser = commands.add_parser("node", help="one class, its neighbours and links out")
    app_args(parser)
    parser.add_argument("name")
    parser.add_argument("level", nargs="?", type=int, help="default: the class's level")
    parser.add_argument("--functions", action="store_true",
                        help="one line per function: its calls out and its callers")
    parser.set_defaults(run=run_node)


def run_view(args):
    app, graph, notes = open_app(args)
    zoom = None
    if args.zoom:
        if len(args.zoom) > 2:
            raise AppError("--zoom takes one class or two (A B)")
        ends = [find_id(graph, notes, n) for n in args.zoom]
        zoom = {"from": ends[0], "to": ends[1] if len(ends) > 1 else None,
                "level": args.zoom_level or args.level}
        add_via(zoom, graph, notes, args)
    expand = [n if n == EXPAND_ALL else find_id(graph, notes, n) for n in args.expand]
    select = selection_id(graph, notes, args.select) if args.select else None
    focus = [selection_id(graph, notes, t) for t in args.focus]
    print(textout.view_text(app.name, graph, notes, level=args.level, zoom=zoom,
                            expand=expand, select=select, focus=focus, **view_options(args)))


def add_view(commands):
    parser = commands.add_parser("view", help="what the viewer shows, as slim text "
                                 "(the viewer's Copy for AI)")
    app_args(parser)
    parser.add_argument("--level", type=int, default=1, help="detail level (0 = domains)")
    parser.add_argument("--zoom", nargs="+", metavar="NAME", help="zoom into A B, or one class A")
    parser.add_argument("--zoom-level", type=int, help="level the zoomed line is seen at "
                        "(default: --level)")
    add_via_arg(parser)
    parser.add_argument("--expand", action="append", default=[], metavar="NAME",
                        help="open a class into its functions (repeat; * = all)")
    parser.add_argument("--select", help="a class, A>B, or Class#function to describe")
    parser.add_argument("--focus", nargs="+", default=[], metavar="TARGET",
                        help="highlight these classes, A>B lines or Class#functions (a spec's or "
                        "tour step's view); function targets open their class")
    parser.set_defaults(run=run_view)


# --- tours (DESIGN 7c) ---

def run_tours(args):
    app, graph, notes = open_app(args)
    rows = open_tours(app, graph, notes).list_json(" ".join(args.query))["tours"]
    print(textout.format_tours(app.name, rows))


def add_tours(commands):
    parser = commands.add_parser("tours", help="list tours (guided walks through the code)")
    app_args(parser, view_options=False)
    parser.add_argument("query", nargs="*", help="words in titles, step text or targets")
    parser.set_defaults(run=run_tours)


def tour_edit(args, tours):
    """--draft, --step N --text/--title, --still-true: the small writes; returns the tour."""
    by, ai = (args.by, False) if args.by else (AI_AUTHOR, True)
    if args.draft is not None:
        if not args.targets:
            raise AppError("--draft needs targets: --draft \"Title\" A A#f A>B ...")
        tour = tours.draft(args.id, args.draft, args.targets, by)
        print(f"drafted {tour.id}: {len(tour.steps)} steps by {by} (AI draft)")
    elif args.still_true:
        keys = [tours.get(args.id).steps[args.step - 1].key] if args.step else None
        tour, steps = tours.still_true(args.id, keys)
        print(f"{tour.id}: still true ({len(steps)} steps)")
    else:
        tour, step = tours.edit_step(args.id, args.step, by, ai, text=args.text, title=args.title)
        print(f"{tour.id} step {args.step}: saved by {by}{' (AI draft)' if ai else ''}")


def run_tour(args):
    app, graph, notes = open_app(args)
    tours = open_tours(app, graph, notes)
    try:
        if args.draft is not None or args.still_true or args.text or args.title:
            if (args.text or args.title) and not args.step:
                raise AppError("--text/--title need --step N")
            return tour_edit(args, tours)
        data = tours.tour_json(tours.get(args.id))
        if args.md:
            text = textout.tour_markdown(app.name, data)
            if args.output:
                Path(args.output).write_text(text, encoding="utf-8")
                return print(f"{args.id} -> {args.output}")
            return print(text, end="")
        print(textout.format_tour(app.name, data, args.step, not args.no_code, args.marked,
                                  args.audio))
    except KeyError as error:
        raise AppError(error.args[0]) from error
    except (ValueError, IndexError) as error:
        raise AppError(str(error)) from error


def add_tour(commands):
    parser = commands.add_parser("tour", help="print a tour (lean: step text and its code lines), "
                                 "export it (--md), draft one (--draft) or fix a step")
    app_args(parser, view_options=False)
    parser.add_argument("id", help="tour id")
    parser.add_argument("targets", nargs="*", help="with --draft: one step per target, in order")
    parser.add_argument("--step", type=int, help="one step only (1-based)")
    parser.add_argument("--marked", action="store_true", help="only the highlighted lines")
    parser.add_argument("--no-code", action="store_true", help="titles, targets and text only")
    parser.add_argument("--audio", action="store_true", help="say which steps have a recording")
    parser.add_argument("--md", action="store_true", help="export as a Markdown document")
    parser.add_argument("-o", "--output", help="with --md: write to this file")
    parser.add_argument("--draft", metavar="TITLE", help="draft a new tour along the targets "
                        "(AI draft until accepted)")
    parser.add_argument("--text", help="with --step: new step text")
    parser.add_argument("--title", help="with --step: new step title")
    parser.add_argument("--still-true", action="store_true", help="the stale steps (or --step N) "
                        "still match today's code")
    parser.add_argument("--by", help="author name (default: claude, saved as an AI draft)")
    parser.set_defaults(run=run_tour)


# --- reading commands: outline, find, impact, brief ---

def find_unit(graph, notes, text):
    """(class id, function or None) for `Name` or `Name#function`; edges are refused."""
    kind, *ids = parse_target(graph, notes, text)
    if kind == "edge":
        raise AppError(f"{text}: give a class or Class#function, not a connection")
    if kind == "function" and not is_function(graph, "#".join(ids)):
        raise AppError(f"{ids[0]} has no function {ids[1]}")
    if ids[0] not in graph.nodes:
        raise AppError(f"{text}: not a class")
    return ids[0], ids[1] if kind == "function" else None


def run_outline(args):
    app, graph, notes = open_app(args)
    node_id, function = find_unit(graph, notes, args.target)
    print(textout.format_outline(app.name, outline(graph, notes, node_id, function), function))


def add_outline(commands):
    parser = commands.add_parser("outline", help="a class's functions and fields with their "
                                 "file:first-last lines, to read only the range you need")
    app_args(parser, view_options=False)
    parser.add_argument("target", help="Name or Name#function")
    parser.set_defaults(run=run_outline)


FIND_LIMIT = 20


def run_find(args):
    app, graph, notes = open_app(args)
    hits = find(graph, notes, args.words, args.tests)
    shown = hits if args.all else hits[:FIND_LIMIT]
    print(textout.format_find(app.name, args.words, hits, shown, unique_namer(graph)))


def add_find(commands):
    parser = commands.add_parser("find", help="search class and function names, notes and code "
                                 "docs for words (all must match), best first")
    app_args(parser, view_options=False)
    parser.add_argument("words", nargs="+")
    parser.add_argument("--all", action="store_true", help=f"every match, not the first {FIND_LIMIT}")
    parser.add_argument("--tests", action="store_true", help="include test code")
    parser.set_defaults(run=run_find)


def run_impact(args):
    app, graph, notes = open_app(args)
    unit = find_unit(graph, notes, args.target)
    print(textout.format_impact(app.name, unit, args.depth, impact(graph, unit, args.depth),
                                unique_namer(graph)))


def add_impact(commands):
    parser = commands.add_parser("impact", help="who depends on a class or Class#function, by "
                                 "distance, and the tests to rerun")
    app_args(parser, view_options=False)
    parser.add_argument("target", help="Name or Name#function")
    parser.add_argument("--depth", type=int, default=IMPACT_DEPTH,
                        help=f"how many steps out (default {IMPACT_DEPTH})")
    parser.set_defaults(run=run_impact)


def run_brief(args):
    app, graph, notes = open_app(args)
    units = [find_unit(graph, notes, t) for t in args.targets]
    print(textout.format_brief(app.name, brief(graph, notes, units), short_namer(graph)))


def add_brief(commands):
    parser = commands.add_parser("brief", help="a compact context block to paste into another "
                                 "agent's brief: ranges, notes, neighbours, tests, warnings")
    app_args(parser, view_options=False)
    parser.add_argument("targets", nargs="+", help="Name or Name#function ...")
    parser.set_defaults(run=run_brief)


# --- tests / warnings ---

def run_tests(args):
    app, graph, notes = open_app(args)
    node_id = find_id(graph, notes, args.name)
    found = files_to_retest(graph, node_id, args.deep)
    name = unique_namer(graph)
    print(f"{app.name} tests {name(node_id)}{' deep' if args.deep else ''} {len(found)}")
    for test_file, via in found:
        print(test_file + (f" (via {name(via)})" if via else ""))


def add_tests(commands):
    parser = commands.add_parser("tests", help="test files to run after changing a class")
    app_args(parser, view_options=False)
    parser.add_argument("name")
    parser.add_argument("--deep", action="store_true", help="add tests of its direct users")
    parser.set_defaults(run=run_tests)


def run_warnings(args):
    app, graph, notes = open_app(args)
    print(textout.format_warnings(app.name, warnings_by_kind(graph, notes, args.all),
                                  unique_namer(graph)))
    tours = open_tours(app, graph, notes)
    for line in textout.tour_warning_lines(tours.stale_steps(), tours.lost_steps()):
        print(line)


def add_warnings(commands):
    parser = commands.add_parser("warnings", help="unused, god classes, cycles, unsure, unresolved, layer, stale notes")
    app_args(parser, view_options=False)
    parser.add_argument("--all", action="store_true", help="also show ignored warnings")
    parser.set_defaults(run=run_warnings)


def rule_text(number, rule):
    filters = "".join(f" {f}={rule[f]}" for f in RULE_FILTERS if f in rule)
    return f"{number}. {rule['glob']} ignore={','.join(rule['ignore'])}{filters}"


def run_rules(args):
    app = find_app(args.app, args.data)
    words = args.words
    if words[:1] == ["remove"]:
        if len(words) != 2 or not words[1].isdigit():
            raise AppError("usage: rules <app> remove N")
        app.remove_rule(int(words[1]))
    elif words[:1] == ["add-layer"]:
        if len(words) != 3 or bool(args.forbid) == bool(args.only):
            raise AppError("usage: rules <app> add-layer FROM TO --forbid|--only [--kind K] [--why TEXT]")
        app.add_layer(words[1], words[2], "forbid" if args.forbid else "only", args.kind, args.why)
    elif words[:1] == ["add"]:
        options = dict(word.partition("=")[::2] for word in words[2:])
        if len(words) < 2 or any("=" not in word for word in words[2:]):
            raise AppError('usage: rules <app> add "<glob>" ignore=kind,kind [lang=..] [kind=..]')
        ignore = options.pop("ignore", "")
        app.add_rule(words[1], [k for k in ignore.split(",") if k], **options)
    elif words:
        raise AppError("usage: rules <app> [add \"<glob>\" ignore=.. [lang=..] [kind=..] "
                       "| add-layer FROM TO --forbid|--only | remove N]")
    rules, layers = app.warning_rules(), app.layer_rules()
    print(f"{app.name} rules {len(rules) + len(layers)}")
    for number, rule in enumerate(rules, 1):
        print(rule_text(number, rule))
    for number, layer in enumerate(layers, len(rules) + 1):
        print(textout.layer_rule_text(number, layer))


def add_rules(commands):
    parser = commands.add_parser("rules", help="warning rules by folder and architecture rules: list, add, "
                                 "add-layer, remove (no rescan needed)")
    app_args(parser, view_options=False)
    parser.add_argument("words", nargs="*", help='add "<glob>" ignore=kind,kind [lang=..] '
                        "[kind=..] | add-layer FROM TO | remove N")
    parser.add_argument("--forbid", action="store_true", help="add-layer: FROM must not depend on TO")
    parser.add_argument("--only", action="store_true", help="add-layer: only FROM may use TO")
    parser.add_argument("--kind", help="add-layer: limit to one line kind (call, inheritance, signal, ...)")
    parser.add_argument("--why", help="add-layer: the reason, shown with the warning")
    parser.set_defaults(run=run_rules)


# --- curate / set ---

def missing_fields(notes, node_id):
    return [f for f in CURATED_FIELDS if not notes.has(node_key(node_id), f)]


def curate_row(graph, notes, node, missing):
    outs = [e for e in graph.edges.values() if e.from_id == node.id and e.to != node.id]
    ins = {e.from_id for e in graph.edges.values() if e.to == node.id and e.from_id != node.id}
    calls = sorted((c for e in outs for c in e.calls), key=lambda c: -c.count)
    top = list(dict.fromkeys(c.member for c in calls))[:TOP_CALLS]
    return (f"{node.id} {node.file} L{effective_level(graph, notes, node.id)} "
            f"{len(ins)}/{len({e.to for e in outs})} -{','.join(missing)}"
            + (" {" + ", ".join(top) + "}" if top else ""))


def run_curate(args):
    app, graph, notes = open_app(args)
    rows = [curate_row(graph, notes, node, missing_fields(notes, node_id))
            for node_id, node in sorted(graph.nodes.items())
            if node.kind != "external" and not is_test(node) and missing_fields(notes, node_id)]
    print(f"{app.name} curate {len(rows)} to do (id file level in/out -missing {{calls}})")
    if rows:
        print("\n".join(rows))


def add_curate(commands):
    parser = commands.add_parser("curate", help="classes still lacking level/domain/hint")
    app_args(parser, view_options=False)
    parser.set_defaults(run=run_curate)


TRUE_WORDS, FALSE_WORDS = ("true", "yes", "1"), ("false", "no", "0")


def parse_value(graph, notes, name, text):
    """A `key=value` value as its note type; empty clears the field."""
    if text == "":
        return None
    if name == "level":
        return int(text)
    if name == "singleton":
        if text.lower() not in TRUE_WORDS + FALSE_WORDS:
            raise AppError(f"singleton must be true or false, got {text}")
        return text.lower() in TRUE_WORDS
    if name == "bindings":
        return [find_id(graph, notes, t.strip()) for t in text.split(",") if t.strip()]
    if name in ("rules", "review"):
        return text.replace("\\n", "\n")  # `\n` in a set line starts a new line
    return clean_value(name, text)


def note_target(graph, notes, target):
    """`Name` -> node key, `A>B` or `A→B` -> edge key, `Name#function` -> function key."""
    kind, *ids = parse_target(graph, notes, target)
    if kind == "edge":
        return edge_key(*ids)
    if kind == "function":
        if not is_function(graph, "#".join(ids)):
            raise AppError(f"{ids[0]} has no function {ids[1]}")
        return function_key(*ids)
    return node_key(ids[0])


def apply_set(graph, notes, tokens, by, ai=None):
    """One `target key=value ...` line; returns (note key, fields kept because they are yours)."""
    if len(tokens) < 2:
        raise AppError(f"need a target and key=value: {' '.join(tokens)}")
    key = note_target(graph, notes, tokens[0])
    fields = {}
    for token in tokens[1:]:
        name, sep, text = token.partition("=")
        if not sep:
            raise AppError(f"not key=value: {token}")
        try:
            fields[name] = parse_value(graph, notes, name, text)
        except ValueError as error:
            raise AppError(f"{token}: {error}") from error
    try:
        return key, notes.set(key, by, fields, ai, graph)
    except ValueError as error:
        raise AppError(str(error)) from error


def set_lines(args):
    if args.file:
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text("utf-8")
        return [shlex.split(line) for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    return [args.items]


def run_still_true(args, app, graph, notes):
    """`set --still-true Name ...`: the targets' notes are still right for today's code."""
    for target in args.items:
        key = note_target(graph, notes, target)
        done = notes.confirm(key, graph)
        print(f"{key}: still true ({', '.join(done) or 'no notes'})")
    app.save_notes(notes)


def run_set(args):
    app, graph, notes = open_app(args)
    if args.still_true:
        return run_still_true(args, app, graph, notes)
    by, ai = (args.by, False) if args.by else (AI_AUTHOR, True)
    results = [apply_set(graph, notes, tokens, by, ai) for tokens in set_lines(args)]
    app.save_notes(notes)
    print(f"set {len(results)} by {by}{' (AI draft)' if ai else ''}")
    for key, kept in results:
        if kept:
            print(f"{key}: kept your {', '.join(kept)}")


def add_set(commands):
    parser = commands.add_parser("set", help="set notes: target key=value ...")
    app_args(parser, view_options=False)
    parser.add_argument("items", nargs="*", help="Name|A>B key=value ...")
    parser.add_argument("--file", help="batch file, one `target key=value ...` per line; - = stdin")
    parser.add_argument("--by", metavar="NAME",
                        help=f"write as this person (default: AI draft by {AI_AUTHOR})")
    parser.add_argument("--still-true", action="store_true",
                        help="items are targets (Name, A>B, Name#fn): keep their notes, mark them "
                             "up to date with the code (clears the stale mark)")
    parser.set_defaults(run=run_set)


def run_serve(args):
    from .server import serve
    try:
        app = find_app(args.app, args.data) if args.app else None
    except AppError as error:
        sys.exit(str(error))
    serve(app, args.port, allow_origin=args.allow_origin)


def add_serve(commands):
    parser = commands.add_parser("serve", help="open the viewer on localhost: every registered "
                                 "app, starting on <app> if given")
    app_args(parser, view_options=False, optional=True)
    parser.add_argument("--port", type=int, default=8517, help="port (default 8517)")
    parser.add_argument("--allow-origin", metavar="URL",
                        help="hosted page origin that may use this server as its helper (default: env "
                             "CODERSTANDING_ALLOW_ORIGIN, then \"origin\" in "
                             "~/.config/coderstanding/hosted.json)")
    parser.set_defaults(run=run_serve)


def run_rescan(args):
    app = find_app(args.app, args.data)
    graph = rescan(app)
    print(f"{app.name}: {len(graph.nodes)} nodes, {len(graph.edges)} edges -> {app.graph_path}")


def add_rescan(commands):
    parser = commands.add_parser("rescan", help="scan an app now into its data folder "
                                 "(what the viewer's Rescan runs, niced)")
    app_args(parser, view_options=False)
    parser.set_defaults(run=run_rescan)


def run_remove(args):
    result = remove_app(args.app, delete_data=args.delete_data, delete_notes=args.delete_notes)
    print(f"removed {result['removed']} from the list")
    if result["deleted"]:
        print(f"deleted in {result['data']}: {', '.join(result['deleted'])}")
    if result["kept"]:
        print(f"kept in {result['data']}: {', '.join(result['kept'])}")


def add_remove(commands):
    parser = commands.add_parser("remove", help="unregister an app (its files stay unless asked)")
    parser.add_argument("app", help="registered name")
    parser.add_argument("--delete-data", action="store_true",
                        help="also delete the scan data Coderstanding made (graph.json, cache/, "
                             "layout.json) in its data folder")
    parser.add_argument("--delete-notes", action="store_true",
                        help="with --delete-data: also delete notes.jsonl, specs/, tours/ and "
                             "settings.json in the data folder")
    parser.set_defaults(run=run_remove)


def run_rename(args):
    print(f"{args.old} -> {rename_app(args.old, args.new)}")


def add_rename(commands):
    parser = commands.add_parser("rename", help="rename an app in the registry (files stay put)")
    parser.add_argument("old")
    parser.add_argument("new")
    parser.set_defaults(run=run_rename)


def build_parser():
    parser = argparse.ArgumentParser(prog="coderstanding")
    commands = parser.add_subparsers(dest="command", required=True)
    for add in (add_scan, add_add, add_settings, add_graph, add_zoom, add_node, add_view, add_tours,
                add_tour, add_outline, add_find, add_impact, add_brief, add_tests,
                add_warnings, add_rules, add_curate, add_set, add_serve, add_rescan,
                add_remove, add_rename):
        add(commands)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.run(args)
    except AppError as error:
        sys.exit(f"error: {error}")


if __name__ == "__main__":
    main()
