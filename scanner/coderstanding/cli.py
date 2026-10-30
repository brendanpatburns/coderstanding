"""Command line: `coderstanding <command> ...`. Add a command with one add_* function."""

import argparse
import os
import importlib.metadata
import shlex
import sys
from collections import Counter
from pathlib import Path

from . import textout
from .textout import short_namer
from .apps import (AppError, load_registry, SETTINGS, find_app, find_id, load_graph, parse_target, register,
                   remove_app, rename_app, rescan, selection_id)
from .graph_json import write_graph
from .cache import SCANNER_VERSION
from .offline import helper_listening, install as install_offline_guard, privacy_report
from .levels import parse_hub_threshold
from .checks import RULE_FILTERS, warnings_by_kind
from .notes import (AI_AUTHOR, HUMAN_AUTHOR, clean_value, edge_key, function_key, is_function,
                    node_key)
from .setup_cli import SETUP_COMMANDS
from .scan import default_output, scan_repo
from .testcode import is_test, files_to_retest
from .cards import KINDS, PRIORITIES, SORTS, link_index, open_cards
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
    known = args.name in load_registry()
    entry = register(args.name, args.path, args.data)
    if args.hub_threshold is not None:
        find_app(args.name).change_settings(scan_settings(args))
    print(f"{args.name} -> {entry['path']}")
    if args.no_scan or (known and not args.rescan):
        return
    from .hub import scan_in_child  # the page's Add project scan: niced child
    result = scan_in_child(find_app(args.name))
    if result["state"] == "error":
        sys.exit(f"{args.name} added, but the scan failed: {result['error']}")
    print(f"{args.name} scanned")


def add_add(commands):
    parser = commands.add_parser("add", help="register an app (repo) by name")
    parser.add_argument("name")
    parser.add_argument("path", help="repo folder")
    parser.add_argument("--data", help="folder for graph.json/notes.jsonl "
                        "(default: <path>/.coderstanding)")
    hub_threshold_option(parser)
    parser.add_argument("--no-scan", action="store_true", help="register only (for scripts)")
    parser.add_argument("--rescan", action="store_true",
                        help="scan even when the name was already registered (re-pointing)")
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
                                 f"({', '.join(SETTINGS)}); a change applies at the next `rescan`")
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
    """(app, graph, notes) from the saved graph (never scans); warns about lost notes."""
    app = find_app(args.app, args.data)
    graph, notes = load_graph(app), app.load_notes()
    app.checked(graph, notes)
    if graph.scanner_version != SCANNER_VERSION:
        print(f"note: made by an older scanner: run `coderstanding rescan {app.name}`", file=sys.stderr)
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
    print_linked(app, graph, notes, f"{zoom['from']}→{zoom['to']}")


def add_zoom(commands):
    parser = commands.add_parser("zoom", help="drill into one connection A -> B")
    app_args(parser)
    parser.add_argument("from_name", metavar="from")
    parser.add_argument("to_name", metavar="to")
    parser.add_argument("--level", type=int, default=1, help="level the line is seen at")
    add_via_arg(parser)
    parser.set_defaults(run=run_zoom)


def print_linked(app, graph, notes, view_id):
    """The specs and memos linking a class or a line `A→B` (cards.link_index)."""
    for line in textout.linked_lines(link_index(app, graph, notes).get(view_id, [])):
        print(line)


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
    print_linked(app, graph, notes, node_id)


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
        if args.comments:
            found = tours.comments.list_json(f"tour:{args.id}")
            return print(textout.format_comments(app.name, data, found, show_resolved=args.all))
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


def allow_options_before_positionals(parser):
    """Make `parser` read positionals that come after an option (what Python 3.12+ does anyway)."""
    plain = parser.parse_known_args

    def parse(args=None, namespace=None):
        parser.parse_known_args = plain  # the intermixed reader calls it for each pass
        try:
            return parser.parse_known_intermixed_args(args, namespace)
        finally:
            parser.parse_known_args = parse
    parser.parse_known_args = parse


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
    parser.add_argument("--comments", action="store_true", help="the open comment threads "
                        "(ids for `comment --reply/--resolve`)")
    parser.add_argument("--all", action="store_true", help="with --comments: resolved ones too")
    # Python 3.11's argparse loses positionals that follow an option (`tour app id --draft T A B`).
    allow_options_before_positionals(parser)
    parser.set_defaults(run=run_tour)


# --- cards: specs and memos (DESIGN 7, 7d); one pair of commands per kind ---

def run_cards(args):
    app, graph, notes = open_app(args)
    cards = open_cards(app, graph, notes, args.kind)
    try:
        data = cards.list_json(args.level, args.zoom, " ".join(args.query), args.stage,
                               args.priority, args.importance, args.sort)
    except KeyError as error:
        raise AppError(error.args[0]) from error
    except ValueError as error:
        raise AppError(str(error)) from error
    print(textout.format_cards(app.name, data))


def add_cards(commands, kind, help_text):
    parser = commands.add_parser(KINDS[kind].folder, help=help_text)
    app_args(parser, view_options=False)
    parser.add_argument("query", nargs="*", help="words to find in titles, text and links "
                        "(any level; prints each path)")
    parser.add_argument("--level", type=int, help="fold finer ones than this level")
    parser.add_argument("--zoom", metavar="ID", help="one item and its children")
    parser.add_argument("--stage", help=f"only this stage ({', '.join(KINDS[kind].stages)})")
    parser.add_argument("--priority", choices=PRIORITIES, help="only this work priority")
    parser.add_argument("--importance", choices=PRIORITIES, help="only this importance")
    parser.add_argument("--sort", choices=SORTS, help="list sorted (priority/importance: high first)")
    parser.set_defaults(run=run_cards, kind=kind)


LIST_FIELDS = ("links", "links+", "links-")
ALIASES = {"status": "stage", "classes": "links", "classes+": "links+", "classes-": "links-"}


def card_value(name, text, old):
    """A `--set name=value` value as the field's type (`links+=A,B>C`, `links-=A`)."""
    name = ALIASES.get(name, name)
    if name in LIST_FIELDS:
        items = [c.strip() for c in text.split(",") if c.strip()]
        if name == "links+":
            return "links", [*old, *items]
        if name == "links-":
            return "links", [c for c in old if c not in items]
        return "links", items
    if name == "level":
        return name, int(text) if text else None
    return name, (text or None) if name in ("stage", "priority", "importance", "parent") else text


def card_values(args, old):
    values = {name: getattr(args, name) for name in ("stage", "priority", "importance", "parent",
                                                     "text", "level")
              if getattr(args, name) is not None}
    if args.links is not None:
        values["links"] = [c.strip() for c in args.links.split(",") if c.strip()]
    for item in args.set or []:
        name, sep, text = item.partition("=")
        if not sep:
            raise AppError(f"not key=value: {item}")
        try:
            name, value = card_value(name.strip(), text, values.get("links", old))
        except ValueError as error:
            raise AppError(f"{item}: {error}") from error
        values[name] = value
    return values


def run_card(args):
    app, graph, notes = open_app(args)
    cards = open_cards(app, graph, notes, args.kind)
    by, ai = (args.by, False) if args.by else (AI_AUTHOR, True)
    try:
        if args.add is not None:
            card_id = args.id or cards.new_id(args.add)
            if card_id in cards.cards:
                raise AppError(f"{args.kind} {card_id} exists: change it with --set")
            card, kept = cards.write(card_id, {"title": args.add, **card_values(args, [])}, by, ai)
        elif not args.id:
            raise AppError(f"give an id (see `coderstanding {cards.kind.folder}`), or --add \"Title\"")
        elif args.delete:
            moved = cards.delete(args.id)
            return print(f"deleted {args.id}" + (f"; moved up: {', '.join(moved)}" if moved else ""))
        elif args.accept:
            card, kept = cards.accept(args.id, by=args.by or HUMAN_AUTHOR), []
        else:
            values = card_values(args, cards.get(args.id).links)
            if not values:
                return print(textout.format_card(app.name, cards.card_json(cards.get(args.id))))
            card, kept = cards.write(args.id, values, by, ai)
    except KeyError as error:
        raise AppError(error.args[0]) from error
    except ValueError as error:
        raise AppError(str(error)) from error
    data = cards.card_json(card)
    print(f"saved {card.id} by {by}{' (AI draft)' if ai and not args.accept else ''}"
          + (f"; kept your {', '.join(kept)}" if kept else ""))
    for problem in data["problems"]:
        print(f"problem: {problem}")


def add_card(commands, kind, help_text):
    parser = commands.add_parser(kind, help=help_text)
    app_args(parser, view_options=False)
    parser.add_argument("id", nargs="?", help="id (with --add: optional, else from the title)")
    parser.add_argument("--add", metavar="TITLE", help="a new one with this title")
    parser.add_argument("--parent", help="parent id (\"\" = top level)")
    parser.add_argument("--links", "--classes", dest="links",
                        help="classes, connections and functions it links: A,B>C,D#f")
    parser.add_argument("--stage", help=f"one of {', '.join(KINDS[kind].stages)}")
    parser.add_argument("--priority", choices=PRIORITIES, help="work priority: how soon")
    parser.add_argument("--importance", choices=PRIORITIES, help="how much it matters")
    parser.add_argument("--level", type=int, help="level (default: parent's + 1)")
    parser.add_argument("--text", help="the text (Markdown, [[wiki links]])")
    parser.add_argument("--set", nargs="+", metavar="KEY=VALUE",
                        help="title=, stage=, priority=, importance=, level=, parent=, text=, "
                             "links=A,B>C, links+=D, links-=A (empty value clears)")
    parser.add_argument("--delete", action="store_true", help="delete it (children move up)")
    parser.add_argument("--accept", action="store_true", help="accept its AI draft fields")
    parser.add_argument("--by", metavar="NAME",
                        help=f"write as this person (default: AI draft by {AI_AUTHOR})")
    parser.set_defaults(run=run_card, kind=kind)


def add_specs(commands):
    add_cards(commands, "spec", "the spec tree (the plan, as nested epics); search, filter "
              "(--stage, --priority, --importance) or sort it")
    add_card(commands, "spec", "print one spec, or add / change / delete / accept one")


def add_memos(commands):
    add_cards(commands, "memo", "the memo tree (the viewer's Notes: comments and information); "
              "search, filter or sort it")
    add_card(commands, "memo", "print one memo, or add / change / delete / accept one")


def comment_change(args, comments, by):
    """--resolve / --reopen ID: the thread; returns the line to print."""
    comment_id = args.resolve or args.reopen
    comments.resolve(comment_id, by, done=bool(args.resolve))
    return f"{'resolved' if args.resolve else 'reopened'} the thread of [{comment_id}] as {by}"


def run_comment(args):
    """Comment on a tour or a step (DESIGN 7d): new, --reply, --line, --resolve, --reopen."""
    app, graph, notes = open_app(args)
    tours = open_tours(app, graph, notes)
    by, ai = (args.by, False) if args.by else (AI_AUTHOR, True)
    try:
        if args.resolve or args.reopen:
            return print(comment_change(args, tours.comments, by))
        if not args.words or (not args.reply and len(args.words) != 2) or len(args.words) > 2:
            raise AppError('usage: comment <app> <target> "<text>" [--line N] | '
                           'comment <app> --reply ID "<text>" | comment <app> --resolve|--reopen ID')
        target = tours.comment_target(args.words[0]) if len(args.words) == 2 else None
        comment = tours.comments.add(target, args.words[-1], by, ai, parent=args.reply,
                                     line=args.line, quote=args.quote)
        where = f" line {args.line}" if args.line else ""
        print(f"added [{comment['id']}] on {comment['target']}{where} as {by}"
              + (" (ai)" if ai else ""))
    except KeyError as error:
        raise AppError(error.args[0]) from error
    except (ValueError, PermissionError) as error:
        raise AppError(str(error)) from error


def add_comment(commands):
    parser = commands.add_parser("comment", help="comment on a tour or a step (tour:<id>, "
                                 "tour:<id>#<step N or key>), reply, resolve or reopen a thread")
    app_args(parser, view_options=False)
    parser.add_argument("words", nargs="*", help='<target> "<text>" (with --reply: just "<text>")')
    parser.add_argument("--reply", metavar="ID", help="reply to this comment's thread")
    parser.add_argument("--line", type=int, help="today's line number in the step's code")
    parser.add_argument("--quote", help="the words of the step text the comment is about")
    parser.add_argument("--resolve", metavar="ID", help="resolve the thread of this comment")
    parser.add_argument("--reopen", metavar="ID", help="reopen the thread of this comment")
    parser.add_argument("--by", help="author name (default: claude, marked as AI)")
    parser.set_defaults(run=run_comment)


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
    for line in textout.tour_warning_lines(tours.stale_steps(), tours.lost_steps(),
                                           tours.comments.lost("tour:")):
        print(line)
    for kind in KINDS:
        for card_id, problem in open_cards(app, graph, notes, kind).all_problems():
            print(f"{kind} problem: {card_id}: {problem}")


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
    from .helper import enter_service_mode, helper_running, log_line, report_service_failure
    if helper_running(args.port):  # before service mode: it must not take over the running one's pid file
        message = f"a helper is already running on http://127.0.0.1:{args.port}/"
        print(message)
        if args.service:
            log_line(message)
        return
    if not args.service:
        return serve(app, args.port, allow_origin=args.allow_origin)
    console = enter_service_mode()
    try:
        serve(app, args.port, allow_origin=args.allow_origin)
    except Exception as error:  # nobody watches the log: say why, then fail
        report_service_failure(error, console)
        sys.exit(1)


def add_serve(commands):
    parser = commands.add_parser("serve", help="open the viewer on localhost: every registered "
                                 "app, starting on <app> if given")
    app_args(parser, view_options=False, optional=True)
    parser.add_argument("--port", type=int, default=8517, help="port (default 8517)")
    parser.add_argument("--allow-origin", metavar="URL",
                        help="hosted page origin that may use this server as its helper (default: env "
                             "CODERSTANDING_ALLOW_ORIGIN, then \"origin\" in "
                             "~/.config/coderstanding/hosted.json)")
    parser.add_argument("--service", action="store_true",
                        help="run as the login helper without a console (Windows): log to the "
                             "helper log file and record the pid")
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
                        help="with --delete-data: also delete notes.jsonl, specs/, memos/, tours/ and "
                             "settings.json in the data folder")
    parser.set_defaults(run=run_remove)


def run_rename(args):
    print(f"{args.old} -> {rename_app(args.old, args.new)}")


def add_rename(commands):
    parser = commands.add_parser("rename", help="rename an app in the registry (files stay put)")
    parser.add_argument("old")
    parser.add_argument("new")
    parser.set_defaults(run=run_rename)


def run_privacy(args):
    from .server import DEFAULT_PORT, HOST
    from .userconfig import ALLOW_ORIGIN_ENV, config_dir, hosted_origin
    port = args.port or DEFAULT_PORT
    origin = hosted_origin()
    source = (f"env {ALLOW_ORIGIN_ENV}" if os.environ.get(ALLOW_ORIGIN_ENV)
              else str(config_dir() / "hosted.json")) if origin else None
    print(privacy_report(HOST, port, origin, source, helper_listening(HOST, port)))


def add_privacy(commands):
    parser = commands.add_parser("privacy", help="show that Coderstanding works fully offline: "
                                 "network guard, bind address, allowed origin, voice policy")
    parser.add_argument("--port", type=int, help="the helper's port (default 8517)")
    parser.set_defaults(run=run_privacy)


def version():
    try:
        return importlib.metadata.version("coderstanding")
    except importlib.metadata.PackageNotFoundError:
        return "(source checkout)"


def build_parser():
    parser = argparse.ArgumentParser(prog="coderstanding")
    parser.add_argument("--version", action="version", version=f"coderstanding {version()}")
    commands = parser.add_subparsers(dest="command", required=True)
    for add in (add_scan, add_add, add_settings, add_graph, add_zoom, add_node, add_view,
                add_specs, add_memos, add_tours, add_tour, add_comment, add_outline, add_find, add_impact,
                add_brief, add_tests,
                add_warnings, add_rules, add_curate, add_set, add_serve, add_rescan,
                add_remove, add_rename, add_privacy, *SETUP_COMMANDS):
        add(commands)
    return parser


def main(argv=None):
    install_offline_guard()  # before anything else runs (DESIGN 18)
    args = build_parser().parse_args(argv)
    try:
        args.run(args)
    except AppError as error:
        sys.exit(f"error: {error}")


if __name__ == "__main__":
    main()
