"""Command line: `coderstanding <command> ...`. Add a command with one add_* function."""

import argparse
import json
import os
import importlib.metadata
import re
import shlex
import sys
from collections import Counter
from pathlib import Path

from . import app_settings, field_rules, refs, textout
from .textout import short_namer
from . import worktrees
from .apps import (AppError, data_home, graph_scan, candidates, data_for_add, load_names, load_registry, RELEVEL_SCOPES, SETTINGS, find_app, load_graph, register,
                   refresh_map, relevel, remove_app, rename_app, rescan, stale_repo_cache, worktree_app)
from .graph_json import write_graph
from .cache import SCANNER_VERSION
from .offline import helper_listening, install as install_offline_guard, privacy_report
from .levels import parse_hub_threshold
from .checks import RULE_FILTERS, parse_errors_by_node, unresolved_by_node, warnings_by_kind
from .diagnose import diagnose_text
from .notes import (AI_AUTHOR, FUNCTION_SEP, HUMAN_AUTHOR, clean_value, edge_key, function_key, is_function,
                    human_author, node_key, FINGERPRINTED)
from .readtargets import read_target_blocks
from .setup_cli import SETUP_COMMANDS
from .scan import default_output, scan_repo
from .e2e_checks import e2e_for_diff
from .name_reach import command_tests
from .testcode import files_to_retest, is_test, is_test_path
from .cards import KINDS, PRIORITIES, SORTS, SPEC, link_index, linked_texts, open_cards
from .tours import open_tours, parse_view_pairs
from .fingerprints import SourceLines, key_ranges
from . import planned, progress
from .views import (ALL_LINES, CONCRETE, EXPLAINED, LINES, EXPAND_ALL, IMPACT_DEPTH, SCENE_TYPE, brief, compute_view, diff_tests, effective_level, find, find_text,
                    find_suggestions, globals_list, hit_callers, hotspots, impact, name_impact, outline, read_blocks, miss, resolve_class, resolve_target, resolve_unit, selection_id,
                    suggest_files, unique_namer, title_namer,
                    setup_of)
from .filewalk import git_changed_lines, git_churn, git_range_commits
from .textindex import dupes, env_vars, read_index, under_path, uses

SPEC_STAGES = SPEC.stages
CURATED_FIELDS = ("level", "domain", "hint")
REDO_FIELDS = CURATED_FIELDS + ("description",)  # `curate --redo` also rewrites AI descriptions
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
    entry = register(args.name, args.path, data_for_add(args.name, args.path, args.data))
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
                        "(default: ~/.local/share/coderstanding/<name>, never inside the repo)")
    hub_threshold_option(parser)
    parser.add_argument("--no-scan", action="store_true", help="register only (for scripts)")
    parser.add_argument("--rescan", action="store_true",
                        help="scan even when the name was already registered (re-pointing)")
    parser.set_defaults(run=run_add)


def key_value_pairs(items):
    """[(key, text)] from `key=value` tokens, in order (a key may repeat: `library+=a library+=b`)."""
    pairs = []
    for token in items:
        name, sep, text = token.partition("=")
        if not sep:
            raise AppError(f"not key=value: {token}")
        pairs.append((name, text))
    return pairs


def key_values(items):
    """{key: text} from `key=value` tokens."""
    return dict(key_value_pairs(items))


def run_settings(args):
    app = find_app(args.app, args.data)
    changes = key_value_pairs(args.items)
    settings = app.change_settings(changes) if changes else {**app.scan_settings(), **app.rule_lists()}
    print(f"{app.name} settings " + " ".join(f"{key}={setting_text(value)}"
                                             for key, value in settings.items() if value not in ([], {}))
          + (f" levels={','.join(app.level_settings()['names'])}" if app.level_settings() else ""))


def setting_text(value):
    """A setting as the user types it: 0 is `off`, a list is comma text."""
    if isinstance(value, list):
        return json.dumps(value) if any(isinstance(item, dict) for item in value) else ",".join(value)
    if isinstance(value, dict):
        return json.dumps(value)
    return "off" if value == 0 else value


def add_settings(commands):
    parser = commands.add_parser("settings", help="show or change an app's settings "
                                 f"({', '.join(app_settings.NAMES)}); scan settings apply at the next `rescan`, "
                                 "the others at once; `name+=item` adds to a list, `name-=key` removes, "
                                 "`name=[json list]` replaces; run `coderstanding guide` for each setting's syntax")
    app_args(parser, view_options=False, worktree=False)
    parser.add_argument("items", nargs="*", help="key=value ...")
    parser.set_defaults(run=run_settings)


# --- shared by the text commands ---

def app_args(parser, view_options=True, optional=False, worktree=True):
    parser.add_argument("app", nargs="?" if optional else None, help="registered name or repo folder")
    parser.add_argument("--data", help="folder holding graph.json/notes.jsonl")
    if worktree:
        parser.add_argument("--repo", metavar="PATH",
                            help="read this git worktree of the app's repo (default: the current "
                            "folder's worktree), so lines match its branch; notes stay the app's")
    if view_options:
        parser.add_argument("--concrete", action="store_true", help="concrete view")
        parser.add_argument("--calls", action="store_true", help="list every call")
        parser.add_argument("--no-scenes", action="store_true", help="same as --hide-type .tscn")
        parser.add_argument("--no-external", action="store_true", help="hide outside code")
        parser.add_argument("--no-comm", action="store_true",
                            help="hide communications nodes (sockets, network endpoints)")
        parser.add_argument("--tests", action="store_true", help="show test code (hidden by default)")
        parser.add_argument("--no-planned", action="store_true",
                            help="hide planned classes, functions and lines (shown by default)")
        parser.add_argument("--hide-type", action="append", default=[], metavar="EXT",
                            help="hide the classes of a file type, e.g. .gd (repeatable)")
        parser.add_argument("--hide-file-kind", action="append", default=[], metavar="KIND",
                            help="hide the classes of a file kind, e.g. Data (repeatable)")
        parser.add_argument("--tag", action="append", default=[], metavar="TAG",
                            help="only the classes with this tag note (repeatable: any of them)")
        parser.add_argument("--hide-tag", action="append", default=[], metavar="TAG",
                            help="hide the classes with this tag note (repeatable)")
        parser.add_argument("--hide-kind", action="append", default=[], metavar="KIND",
                            help="hide the lines of a kind, e.g. signal (repeatable)")
        parser.add_argument("--lines", choices=(EXPLAINED, ALL_LINES), default=EXPLAINED,
                            help="explained (default): lines with a hint first, the rest marked "
                            "quiet; all: every line equal")


def open_app(args, warn_lost=True):
    """(app, graph, notes) from the saved graph; the map (main or a worktree's own) is rescanned
    first when the files changed since it was built, so nothing answers from an old map (the
    server never does this: scans are manual there). Explains lost notes unless `warn_lost` is off
    (find: they do not change its answer)."""
    app = worktree_app(find_app(args.app, args.data), args.repo)
    rescanned, stale = refresh_map(app)
    folder = app.map_dir or app.data
    if app.map_dir:
        first_time = worktrees.changed_since_shown(folder, "worktree", str(app.root))
        if rescanned or first_time:
            print(f"note: worktree {app.root}{' (rescanned)' if rescanned else ''}", file=sys.stderr)
    elif rescanned and worktrees.changed_since_shown(folder, "rescanned", app.graph_path.stat().st_mtime_ns):
        print("note: map was older than the files: rescanned", file=sys.stderr)
    if stale and worktrees.changed_since_shown(folder, "stale", stale):
        print(f"note: map is older than the files ({stale} changed): rescan", file=sys.stderr)
    graph, notes = load_graph(app), app.load_notes()
    app.checked(graph, notes)
    if graph.scanner_version != SCANNER_VERSION:
        print(f"note: made by an older scanner: run `coderstanding rescan {app.name}`", file=sys.stderr)
    lost = notes.lost(graph)
    if warn_lost and worktrees.changed_since_shown(app.map_dir or app.data, "lost_notes", sorted(lost)) and lost:
        print(f"note: {len(lost)} saved notes belong to renamed or removed code (kept, harmless "
              f"unless you expected them): {', '.join(lost[:3])}{' ...' if len(lost) > 3 else ''}",
              file=sys.stderr)
    return app, graph, notes


def hidden_types(args):
    hidden = [t.lower() for t in args.hide_type]
    return hidden + [SCENE_TYPE] if args.no_scenes and SCENE_TYPE not in hidden else hidden


def view_options(args):
    return {"view": CONCRETE if args.concrete else "all",
            "show": {"external": not args.no_external, "comm": not args.no_comm, "test": args.tests,
                     "planned": not getattr(args, "no_planned", False),
                     "hide_types": hidden_types(args), "hide_file_kinds": args.hide_file_kind,
                     "hide_kinds": args.hide_kind, "tags": getattr(args, "tag", []),
                     "hide_tags": getattr(args, "hide_tag", []),  # hand-made args (tests) may lack them
                     LINES: args.lines}}


def run_graph(args):
    app, graph, notes = open_app(args)
    options = view_options(args)
    view = compute_view(graph, notes, level=args.level, **options)
    outside = 0
    if options["show"]["external"] and not args.all:  # own code only; outside lines are a count
        own = compute_view(graph, notes, level=args.level, **{**options, "show": {**options["show"], "external": False}})
        outside, view = len(view["edges"]) - len(own["edges"]), own
    print(textout.format_graph(app.name, view, args.calls, short_namer(graph, notes), graph))
    if outside:
        print(f"+{outside} lines to outside packages (--all)")
    globals_text = textout.globals_lines(globals_list(graph))
    if globals_text:
        print("\n".join(globals_text))


def add_graph(commands):
    parser = commands.add_parser("graph", help="print the graph at a level as slim text")
    app_args(parser)
    parser.add_argument("level", nargs="?", type=int, default=1, help="0 = domains")
    parser.add_argument("--all", action="store_true", help="also the lines to outside packages")
    parser.set_defaults(run=run_graph)


def add_via(zoom, graph, notes, args):
    """Set zoom["via"] to the --via classes; unset it so the engine finds the path."""
    if args.via:
        zoom["via"] = [resolve_class(graph, notes, n) for n in args.via]


def add_via_arg(parser):
    parser.add_argument("--via", action="append", default=[], metavar="NAME",
                        help="hidden class the zoomed line runs through (repeat)")


def run_zoom(args):
    app, graph, notes = open_app(args)
    graph = planned.hand_graph(graph, notes, not getattr(args, "no_planned", False))
    zoom = {"from": resolve_class(graph, notes, args.from_name),
            "to": resolve_class(graph, notes, args.to_name), "level": args.level}
    add_via(zoom, graph, notes, args)
    view = compute_view(graph, notes, zoom=zoom, **view_options(args))
    print(textout.format_zoom(app.name, view, args.calls, short_namer(graph, notes)))
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
    graph = planned.hand_graph(graph, notes, not getattr(args, "no_planned", False))  # a planned class is a node too
    node_id = resolve_class(graph, notes, args.name)
    level = args.level
    if level is None:
        level = effective_level(graph, notes, node_id) if node_id in graph.nodes else 0
    zoom = {"from": node_id, "to": None, "level": level}
    if args.functions:  # every shown class open, so callers show their functions too
        view = compute_view(graph, notes, zoom=zoom, expand=[EXPAND_ALL], **view_options(args))
        print(textout.format_functions(app.name, view, node_id, short_namer(graph, notes)))
        return
    view = compute_view(graph, notes, zoom=zoom, **view_options(args))
    print(textout.format_node(app.name, view, node_id, args.calls, short_namer(graph, notes)))
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
        ends = [resolve_class(graph, notes, n) for n in args.zoom]
        zoom = {"from": ends[0], "to": ends[1] if len(ends) > 1 else None,
                "level": args.zoom_level or args.level}
        add_via(zoom, graph, notes, args)
    expand = [n if n == EXPAND_ALL else resolve_class(graph, notes, n) for n in args.expand]
    select = selection_id(graph, notes, args.select) if args.select else None
    focus = [selection_id(graph, notes, t) for t in args.focus]
    print(textout.view_text(app.name, graph, notes, level=args.level, zoom=zoom,
                            expand=expand, select=select, focus=focus, domain=args.domain,
                            **view_options(args)))


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
    parser.add_argument("--domain", help="show only this domain's classes (Unsorted = no domain), "
                        "the other domains as faded cards")
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
    """--draft, --step N --text/--title/--note/--drop-note, --still-true: the small writes."""
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
    elif args.view:
        tour, view = tours.set_view(args.id, parse_view_pairs(args.view), args.step)
        where = f"step {args.step}" if args.step else "tour"
        print(f"{tour.id} {where} view: {textout.view_text_of(view) or '(none)'}")
    elif args.note or args.drop_note:
        lines, text = args.note or (args.drop_note, None)
        tour, step = tours.edit_note(args.id, args.step, lines, text, args.cue, by, ai)
        what = "note saved" if text is not None else "note removed"
        print(f"{tour.id} step {args.step}: {what} on lines {lines} by {by}{' (AI draft)' if ai else ''}"
              f" ({len(step.notes)} notes)")
    else:
        tour, step = tours.edit_step(args.id, args.step, by, ai, text=args.text, title=args.title)
        print(f"{tour.id} step {args.step}: saved by {by}{' (AI draft)' if ai else ''}")


def draft_starts(tours, title):
    """--draft-start [TITLE]: AI-draft the start tours from the map's main paths."""
    drafted, kept = tours.draft_starts(title or None)
    for tour in drafted:
        print(f"drafted {tour.id}: {len(tour.steps)} steps by {AI_AUTHOR} (AI draft, start tour)")
    for tour_id in kept:
        print(f"{tour_id} exists: left alone (edit it or delete its file)")
    if not drafted and not kept:
        print("no start path: no entry class with a line leading on at level 1")


def run_tour(args):
    app, graph, notes = open_app(args)
    tours = open_tours(app, graph, notes)
    try:
        notes = args.note or args.drop_note
        if args.cue and not args.note:
            raise AppError("--cue goes with --note LINES TEXT")
        if args.draft_start is not None:
            return draft_starts(tours, args.draft_start)
        if not args.id:
            raise AppError("tour needs an id (see `coderstanding tours`) or --draft-start")
        if args.draft is not None or args.still_true or args.text or args.title or notes or args.view:
            if (args.text or args.title or notes) and not args.step:
                raise AppError("--text/--title/--note/--drop-note need --step N")
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
    parser.add_argument("id", nargs="?", help="tour id (not with --draft-start)")
    parser.add_argument("targets", nargs="*", help="with --draft: one step per target, in order")
    parser.add_argument("--step", type=int, help="one step only (1-based)")
    parser.add_argument("--marked", action="store_true", help="only the highlighted lines")
    parser.add_argument("--no-code", action="store_true", help="titles, targets and text only")
    parser.add_argument("--audio", action="store_true", help="say which steps have a recording")
    parser.add_argument("--md", action="store_true", help="export as a Markdown document")
    parser.add_argument("-o", "--output", help="with --md: write to this file")
    parser.add_argument("--draft", metavar="TITLE", help="draft a new tour along the targets "
                        "(AI draft until accepted)")
    parser.add_argument("--draft-start", nargs="?", const="", metavar="TITLE", help="draft the "
                        "\"Start here\" tours (start: true) from the main path out of each entry "
                        "class (no id or targets; the optional title names the first)")
    parser.add_argument("--text", help="with --step: new step text")
    parser.add_argument("--title", help="with --step: new step title")
    parser.add_argument("--note", nargs=2, metavar=("LINES", "TEXT"), help="with --step: a line "
                        "note (popup in the viewer's code pane) on today's lines, e.g. 50-51 \"why\"; "
                        "same lines again replaces it")
    parser.add_argument("--cue", metavar="WORDS", help="with --note: words of the step text the "
                        "note pops up at (default: in order through the step)")
    parser.add_argument("--drop-note", metavar="LINES", help="with --step: remove the note on these lines")
    parser.add_argument("--view", nargs="+", metavar="KEY=VALUE", help="view options while the tour "
                        "plays (the viewer's URL names: lines=all selfcalls=0 boxtext=full "
                        "external=0 hide_kind=signal,dependency ...; key= removes one); with --step N: "
                        "that step only")
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

def run_outline(args):
    app, graph, notes = open_app(args)
    node_id, function = resolve_unit(graph, notes, args.target)
    print(textout.format_outline(app.name, outline(graph, notes, node_id, function), function,
                                 title_namer(graph, notes)))


def run_read(args):
    app, graph, notes = open_app(args)
    source, namer = SourceLines(app.root).of, unique_namer(graph)
    plain = [t for t in args.targets if not t.startswith("@")]
    for target in args.targets:  # a pasted ref (`@app ...`) prints as `ref` does
        if target.startswith("@"):
            print_refs(target)
    blocks = []
    for target in plain:
        blocks += read_target_blocks(app, graph, notes, target, args.signatures, args.context,
                                     source, namer)
    if plain:
        print(textout.format_read(app.name, plain, blocks, source, namer))


def run_ref(args):
    text = sys.stdin.read() if args.text == ["-"] else "\n".join(args.text)
    if not print_refs(text):
        raise AppError("no ref found: a ref is `@<app> <target>`, e.g. @myapp src/a.py:10-20")


def print_refs(text):
    """Print every ref in `text` (one block each; a ref that no longer resolves prints its error
    and nearest targets); returns how many refs there were."""
    found = refs.find_refs(text)
    opened = {}
    for parsed in found:
        label = refs.format_ref(parsed)
        try:
            if parsed["app"] not in opened:
                opened[parsed["app"]] = open_app(argparse.Namespace(app=parsed["app"], data=None,
                                                                    repo=None))
            print(refs.ref_text(*opened[parsed["app"]], parsed))
        except refs.RefMiss as error:
            near = f"; did you mean: {', '.join(error.did_you_mean)}" if error.did_you_mean else ""
            print(f"{label}\nerror: {error}{near}")
        except AppError as error:
            print(f"{label}\nerror: {error}")
    return len(found)


def add_ref(commands):
    parser = commands.add_parser("ref", help="resolve pasted location refs (`@<app> <target>` "
                                 "lines the viewer copies) and print what they point at")
    parser.add_argument("text", nargs="+", help="the pasted text (junk around refs is ignored; "
                        "'-' reads stdin)")
    parser.set_defaults(run=run_ref)


def add_read(commands):
    parser = commands.add_parser("read", help="print the source of functions or classes with line "
                                 "numbers, their file:lines and hint")
    app_args(parser, view_options=False)
    parser.add_argument("targets", nargs="+", metavar="target",
                        help="Class#function, Class, a bare function name, path:line (the function "
                        "around it), path:first-last, path (header + definitions), doc.md#Heading, "
                        "or a pasted ref ('@app ...', see `ref`)")
    parser.add_argument("--signatures", action="store_true",
                        help="only each function's first line (a class's function list as code)")
    parser.add_argument("--context", type=int, metavar="N",
                        help="N more lines around each (a path:line outside functions: 5)")
    allow_options_before_positionals(parser)
    parser.set_defaults(run=run_read)


def add_outline(commands):
    parser = commands.add_parser("outline", help="a class's functions and fields with their "
                                 "file:first-last lines, to read only the range you need")
    app_args(parser, view_options=False)
    parser.add_argument("target", help="Name or Name#function")
    parser.set_defaults(run=run_outline)


def find_test_file(graph, text):
    """Repo path of the test file named by `text` (a path, or a path ending the same way)."""
    files = sorted({n.file for n in graph.nodes.values() if n.kind != "external"
                    and (n.file == text or n.file.endswith("/" + text))})
    if not files:
        raise miss(f"{text}: no such file in the scan", suggest_files(graph, text))
    if len(files) > 1:
        raise AppError(f"{text}: several files match: {', '.join(files[:5])}")
    if not is_test_path(files[0]) and not any(is_test(n) for n in graph.nodes.values()
                                              if n.file == files[0]):
        raise AppError(f"{files[0]} is not a test file")
    return files[0]


def run_setup(args):
    app, graph, notes = open_app(args)
    print(textout.format_setup(app.name, setup_of(graph, notes, find_test_file(graph, args.file))))


def add_setup(commands):
    parser = commands.add_parser("setup", help="a test file's helpers, hooks, bases and globals "
                                 "(and how it reaches them), to copy when writing a new test")
    app_args(parser, view_options=False)
    parser.add_argument("file", help="the test file's path (a trailing part is enough)")
    parser.set_defaults(run=run_setup)


FIND_LIMIT = 20


def run_find_text(args, app, graph, notes):
    if args.words:
        raise AppError(f"find: words or --text, not both: find {app.name} <words...>  |  "
                       f"find {app.name} --text <pattern> [--path <prefix>]")
    rows = find_text(graph, load_names(app), app.root, args.text, args.path)
    print(textout.format_find_text(app.name, args.text, rows, args.all, title_namer(graph, notes)))


def run_find(args):
    app, graph, notes = open_app(args, warn_lost=False)
    if args.text is not None:
        return run_find_text(args, app, graph, notes)
    if not args.words:
        raise AppError(f"find: give words or --text: find {app.name} <words...>  |  "
                       f"find {app.name} --text <pattern> [--path <prefix>]")
    hits = find(graph, notes, args.words, args.include_tests, linked_texts(app, graph, notes))
    shown = hits if args.all else hits[:FIND_LIMIT]
    extras = hit_callers(graph, shown) if args.tests else None
    print(textout.format_find(app.name, args.words, hits, shown, title_namer(graph, notes),
                              find_suggestions(graph, args.words) if not hits else (), extras))


def add_find(commands):
    parser = commands.add_parser("find", help="search class, function and field names plus their "
                                 "notes and the spec / memo / tour text about them, fuzzy words, best "
                                 "first; --text searches file text instead")
    app_args(parser, view_options=False)
    parser.add_argument("words", nargs="*")
    parser.add_argument("--text", metavar="PATTERN",
                        help="instead of names: lines of any indexed file (code, docs, scripts, "
                        "config) matching this regex or literal, with their enclosing function / heading")
    parser.add_argument("--path", default="", metavar="PREFIX",
                        help="with --text: only files whose path starts with this")
    parser.add_argument("--all", action="store_true",
                        help=f"every match, not the first {FIND_LIMIT} (--text: every line, not "
                        f"{textout.MAX_TEXT_HITS} then a count per file)")
    parser.add_argument("--tests", action="store_true",
                        help="per hit: its callers and the nearest test files using it")
    parser.add_argument("--include-tests", action="store_true", help="also search test code")
    parser.set_defaults(run=run_find)


def is_plain_name(graph, text):
    """True for a name no class matches (a constant, env var, settings key)."""
    return "#" not in text and text not in graph.nodes and not candidates(graph, text)


def run_impact(args):
    app, graph, notes = open_app(args)
    index = read_index(app.graph_path)  # None before the first rescan with names: classes still work
    rows = is_plain_name(graph, args.target) and uses(load_names(app), app.root, args.target)
    if rows:
        unit, result = args.target, name_impact(graph, rows, args.depth)
    else:
        unit = resolve_unit(graph, notes, args.target)
        result = impact(graph, unit, args.depth, index, command_tests_finder(app, graph))
        result["tours"] = open_tours(app, graph, notes).steps_over([unit])
    print(textout.format_impact(app.name, unit, args.depth, result, title_namer(graph, notes), args.all))


def add_impact(commands):
    parser = commands.add_parser("impact", help="who depends on a class, Class#function or any "
                                 "name (constant, env var), by distance, the tests to rerun, and "
                                 "the tests and docs naming it")
    app_args(parser, view_options=False)
    parser.add_argument("target", help="Name, Name#function, or a constant / env var name")
    parser.add_argument("--depth", type=int, default=IMPACT_DEPTH,
                        help=f"how many steps out (default {IMPACT_DEPTH})")
    parser.add_argument("--all", action="store_true",
                        help="every test to rerun, not just those reaching the unit directly")
    parser.set_defaults(run=run_impact)


# --- names index: uses, env, dupes ---

def run_uses(args):
    app = find_app(args.app, args.data)
    rows = uses(load_names(app), app.root, args.name)
    print(textout.format_uses(app.name, args.name, rows, args.all))


def add_uses(commands):
    parser = commands.add_parser("uses", help="every line naming a class, function, constant, env "
                                 "var or settings key, in code, tests, docs, deploy and scripts")
    app_args(parser, view_options=False)
    parser.add_argument("name", help="e.g. Player, MAX_PLAYERS, WOOF_PORT, audio/volume")
    parser.add_argument("--all", action="store_true",
                        help=f"every line (docs, scripts, config: not one count line per kind)")
    parser.set_defaults(run=run_uses)


def run_env(args):
    app = find_app(args.app, args.data)
    print(textout.format_env(app.name, env_vars(load_names(app), args.prefix)))


def add_env(commands):
    parser = commands.add_parser("env", help="env vars: where read, set (compose, Dockerfile, "
                                 ".env, scripts) and documented, with gaps")
    app_args(parser, view_options=False)
    parser.add_argument("--prefix", default="", help="only names starting with this, e.g. WOOF_")
    parser.set_defaults(run=run_env)


def run_dupes(args):
    app = find_app(args.app, args.data)
    kinds = ("src", "script", "test") if args.tests else ("src", "script")
    print(textout.format_dupes(app.name, dupes(load_names(app), kinds), args.all))


def add_dupes(commands):
    parser = commands.add_parser("dupes", help="identical string literals in 2+ files, "
                                 "most repeated first")
    app_args(parser, view_options=False)
    parser.add_argument("--all", action="store_true",
                        help=f"every literal, not the first {textout.MAX_DUPES}")
    parser.add_argument("--tests", action="store_true", help="include test code")
    parser.set_defaults(run=run_dupes)


def run_brief(args):
    app, graph, notes = open_app(args)
    units = [resolve_unit(graph, notes, t) for t in args.targets]
    print(textout.format_brief(app.name, brief(graph, notes, units), short_namer(graph, notes)))


def add_brief(commands):
    parser = commands.add_parser("brief", help="a compact context block to paste into another "
                                 "agent's brief: ranges, notes, neighbours, tests, warnings")
    app_args(parser, view_options=False)
    parser.add_argument("targets", nargs="+", help="Name or Name#function ...")
    parser.set_defaults(run=run_brief)


# --- tests / warnings ---

def run_progress(args):
    """`progress <app>`: every track's numbers; --track NAME adds domains, trend, what is left;
    --spec ID counts that spec's linked classes and functions."""
    app, graph, notes = open_app(args)
    tracks = progress.Progress(graph, notes)
    if args.spec:
        try:
            targets = open_cards(app, graph, notes, "spec").tree_focus(args.spec)
        except KeyError as error:
            raise AppError(error.args[0]) from error
        print(textout.format_progress(f"{app.name} spec {args.spec}", tracks.links_summary(targets), tracks.problems))
        return
    report = tracks.json(args.track, progress.read_history(app.map_dir or app.data), graph_scan(app))
    if not args.track:
        print(textout.format_progress(app.name, report["tracks"], tracks.problems))
        return
    track = tracks.track(args.track)
    if track is None:
        raise AppError(f"no track {args.track} (have: {', '.join(t['name'] for t in tracks.tracks)})")
    namer = title_namer(tracks.graph, notes)
    todo = [namer(owner) + (f"#{namer.function(owner, function)}" if function else "")
            for owner, _, function in (t.partition(FUNCTION_SEP) for t in tracks.todo(track))]
    row = next(r for r in report["tracks"] if r["name"] == track["name"])
    print(textout.format_track(app.name, row, report["domains"], todo, args.all, report["trend"]))


def add_progress(commands):
    parser = commands.add_parser("progress", help="how much is done: one line per progress track "
                                 "(built in: Described, Tested, Built; own ones in settings.json \"tracks\")")
    app_args(parser, view_options=False)
    parser.add_argument("--track", metavar="NAME", help="one track: per domain, the trend and what is not done")
    parser.add_argument("--spec", metavar="ID", help="the tracks over one spec's linked classes and functions")
    parser.add_argument("--all", action="store_true", help="--track: list every item not done")
    parser.set_defaults(run=run_progress)


def run_hotspots(args):
    app, graph, notes = open_app(args)
    churn = git_churn(app.root, args.days)
    if churn is None:
        sys.exit(f"hotspots needs git history: {app.root} is not in a git repo")
    rows = hotspots(graph, churn)
    shown = rows[:args.top]
    print(textout.format_hotspots(app.name, args.days, shown, len(rows), title_namer(graph, notes)))


def add_hotspots(commands):
    parser = commands.add_parser("hotspots", help="classes and modules ranked by size x git churn x missing "
                                 "tests: where bugs and rework are likely")
    app_args(parser, view_options=False)
    parser.add_argument("--top", type=int, default=15, help="rows to show (default 15)")
    parser.add_argument("--days", type=int, default=90, help="count commits from the last N days (default 90)")
    parser.set_defaults(run=run_hotspots)


def command_tests_finder(app, graph):
    """extra_tests for diff_tests / impact: the tests reaching a unit through a CLI command
    (name_reach.py)."""
    return lambda unit, test_index: command_tests(graph, app.root, unit, test_index)


def run_diff_tests(args, app, graph, notes):
    git = git_changed_lines(app.root, args.diff or None)
    if git is None:
        sys.exit(f"tests --diff needs git: {app.root} is not in a git repo")
    changed, ref = git
    result = diff_tests(graph, changed, command_tests_finder(app, graph))
    index = read_index(app.graph_path)
    if index is not None:
        result["e2e"] = e2e_for_diff(index, app.root, changed)
    result["tours"] = open_tours(app, graph, notes).steps_over(result["units"])
    print(textout.format_diff_tests(app.name, ref[:10], result, title_namer(graph, notes)))


def run_tests(args):
    app, graph, notes = open_app(args)
    if args.diff is not None:
        return run_diff_tests(args, app, graph, notes)
    if not args.name:
        sys.exit("tests needs a class name, or --diff")
    node_id = resolve_class(graph, notes, args.name)
    found = files_to_retest(graph, node_id, args.deep)
    name = title_namer(graph, notes)
    print(f"{app.name} tests {name(node_id)}{' deep' if args.deep else ''} {len(found)}")
    for test_file, via in found:
        print(test_file + (f" (via {name(via)})" if via else ""))


def add_tests(commands):
    parser = commands.add_parser("tests", help="test files to run after changing a class")
    app_args(parser, view_options=False)
    parser.add_argument("name", nargs="?")
    parser.add_argument("--deep", action="store_true", help="add tests of its direct users")
    parser.add_argument("--all", action="store_true", help="no effect with --diff: it always lists every test file")
    parser.add_argument("--diff", nargs="?", const="", metavar="REF",
                        help="instead of a name: the tests to run for the git diff against REF "
                        "(default: merge base with main), uncommitted changes included")
    parser.set_defaults(run=run_tests)


def run_unresolved_names(args, app, graph, notes):
    only_id = resolve_class(graph, notes, args.names) if args.names else None
    short_name = title_namer(graph, notes)
    lines = textout.unresolved_name_lines(app.name, unresolved_by_node(graph, only_id), short_name)
    lines += textout.parse_error_lines(parse_errors_by_node(graph, only_id),
                                       lambda place: source_line_at(app.root, place), short_name)
    print("\n".join(lines))


def source_line_at(root, place):
    """The text of the line a `file:line` place names ('' if the file cannot be read)."""
    file, _, line_number = place.rpartition(":")
    try:
        return (root / file).read_text(encoding="utf-8", errors="replace").splitlines()[int(line_number) - 1]
    except (OSError, ValueError, IndexError):
        return ""


STALE_COMMITS_SHOWN = 3
STALE_RANGES_QUERIED = 3  # a connection can have dozens of call lines: ask git about the first few


def range_places(ranges):
    """`file:first-last` per file, spanning all that file's ranges (a connection's scattered lines)."""
    spans = {}
    for file, first, last in ranges:
        low, high = spans.get(file, (first, last))
        spans[file] = (min(low, first), max(high, last))
    return [f"{file}:{low}" + (f"-{high}" if high != low else "") for file, (low, high) in spans.items()]


def stale_detail_row(app, graph, notes, source, changed, key, stale_fields):
    """What `--stale-detail` shows for one note key (see textout.stale_detail_lines). Only a
    fingerprint is stored, not the old code, so "what changed" is the recent commits on the range."""
    ranges = key_ranges(graph, source, key)
    commits = {}
    for file, first, last in ranges[:STALE_RANGES_QUERIED]:
        for commit in git_range_commits(app.root, file, first, last, STALE_COMMITS_SHOWN):
            commits.setdefault(commit[0], commit)
    uncommitted = any(start <= last and first <= end for file, first, last in ranges
                      for start, end in (changed or {}).get(file, ()))
    notes_shown = [(name, notes.get(key, name)[0], name in stale_fields) for name in FINGERPRINTED
                   if notes.get(key, name)[0]]
    return {"key": key, "notes": notes_shown, "commits": list(commits.values())[:STALE_COMMITS_SHOWN],
            "places": range_places(ranges),
            "uncommitted": uncommitted}


def run_stale_detail(app, graph, notes):
    source = SourceLines(app.root)
    changed = (git_changed_lines(app.root) or ({},))[0]
    rows = [stale_detail_row(app, graph, notes, source, changed, key, fields)
            for key, fields in notes.stale_notes(graph)]
    print("\n".join(textout.stale_detail_lines(app.name, rows, title_namer(graph, notes))))


def run_warnings(args):
    app, graph, notes = open_app(args)
    if args.names is not None:
        return run_unresolved_names(args, app, graph, notes)
    if args.stale_detail:
        return run_stale_detail(app, graph, notes)
    print(textout.format_warnings(app.name, warnings_by_kind(graph, notes, args.all),
                                  title_namer(graph, notes)))
    for line in textout.outside_lines(graph.stack, args.all):
        print(line)
    tours = open_tours(app, graph, notes)
    for line in textout.tour_warning_lines(tours.stale_steps(), tours.lost_steps(),
                                           tours.comments.lost("tour:")):
        print(line)
    for kind in KINDS:
        for card_id, problem in open_cards(app, graph, notes, kind).all_problems():
            print(f"{kind} problem: {card_id}: {problem}")
    if graph.diagnostics.get("imports"):
        print(f"hint: `coderstanding diagnose {app.name}` sums up imports that found no file")


def add_warnings(commands):
    parser = commands.add_parser("warnings", help="unused, god classes, cycles, unsure, unresolved, layer, stale notes; "
                                 "plus an outside line (host-program modules, asset files)")
    app_args(parser, view_options=False)
    parser.add_argument("--all", action="store_true", help="also show ignored warnings and every outside name")
    parser.add_argument("--names", nargs="?", const="", metavar="CLASS",
                        help="instead: list the lines the grammar failed to parse and the unresolved names with their counts, per class (or just CLASS)")
    parser.add_argument("--stale-detail", action="store_true",
                        help="instead: per stale note its code place, all its note texts and the recent commits on that code")
    parser.set_defaults(run=run_warnings)


def run_diagnose(args):
    _, graph, _ = open_app(args)
    for line in diagnose_text(graph, args.examples):
        print(line)


def add_diagnose(commands):
    parser = commands.add_parser("diagnose", help="why classes look unconnected: counts of import "
                                 "patterns that found no file, safe to paste in a public issue")
    app_args(parser, view_options=False)
    parser.add_argument("--examples", action="store_true",
                        help="add a few short anonymised examples (names replaced by letters)")
    parser.set_defaults(run=run_diagnose)


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
    app_args(parser, view_options=False, worktree=False)
    parser.add_argument("words", nargs="*", help='add "<glob>" ignore=kind,kind [lang=..] '
                        "[kind=..] | add-layer FROM TO | remove N")
    parser.add_argument("--forbid", action="store_true", help="add-layer: FROM must not depend on TO")
    parser.add_argument("--only", action="store_true", help="add-layer: only FROM may use TO")
    parser.add_argument("--kind", help="add-layer: limit to one line kind (call, inheritance, signal, ...)")
    parser.add_argument("--why", help="add-layer: the reason, shown with the warning")
    parser.set_defaults(run=run_rules)


# --- curate / set ---

def missing_fields(notes, node):
    """Curated fields not set yet; a domain from a folder rule counts as set."""
    return [f for f in CURATED_FIELDS
            if not (notes.domain_of(node) if f == "domain" else notes.has(node_key(node.id), f))]


def ai_draft_fields(notes, node_id):
    """Redo set: fields whose only value is an AI draft (a human value is never listed)."""
    key = node_key(node_id)
    return [f for f in REDO_FIELDS if notes.get(key, f)[1] is not None and notes.is_ai(key, notes.get(key, f)[1])]


def todo_marks(missing, drafts):
    """`-level,domain ~hint,description`: `-` missing fields, `~` AI drafts to rewrite."""
    marks = ([f"-{','.join(missing)}"] if missing else []) + ([f"~{','.join(drafts)}"] if drafts else [])
    return " ".join(marks)


def curate_row(graph, notes, node, marks):
    outs = [e for e in graph.edges.values() if e.from_id == node.id and e.to != node.id]
    ins = {e.from_id for e in graph.edges.values() if e.to == node.id and e.from_id != node.id}
    calls = sorted((c for e in outs for c in e.calls), key=lambda c: -c.count)
    top = list(dict.fromkeys(c.member for c in calls))[:TOP_CALLS]
    return (f"{node.id} {node.file} L{effective_level(graph, notes, node.id)} "
            f"{len(ins)}/{len({e.to for e in outs})} {marks}"
            + (" {" + ", ".join(top) + "}" if top else ""))


def curate_paths(args):
    """The --path prefixes (repeatable, each may be a comma list)."""
    return [prefix for value in args.path for prefix in value.split(",") if prefix]


def curate_todo(graph, notes, args):
    """(node, marks) of every class to curate, in id order."""
    prefixes = curate_paths(args)
    todo = []
    for node_id, node in sorted(graph.nodes.items()):
        if node.kind == "external" or is_test(node):
            continue
        if prefixes and not any(under_path(node.file, prefix) for prefix in prefixes):
            continue
        missing = missing_fields(notes, node)
        drafts = ai_draft_fields(notes, node_id) if args.redo else []
        if missing or drafts:
            todo.append((node, todo_marks(missing, drafts)))
    return todo


def run_curate(args):
    app, graph, notes = open_app(args)
    rows = [curate_row(graph, notes, node, marks) for node, marks in curate_todo(graph, notes, args)]
    legend = " ~AI draft to rewrite" if args.redo else ""
    print(f"{app.name} curate {len(rows)} to do (id file level in/out -missing{legend} {{calls}})")
    print("\n".join(field_rules.reminder_lines(field_rules.effective(app.data))))
    if rows:
        print("\n".join(rows))


def add_curate(commands):
    parser = commands.add_parser("curate", help="classes still lacking level/domain/hint")
    app_args(parser, view_options=False)
    parser.add_argument("--redo", action="store_true",
                        help="also list classes whose level/domain/hint/description are AI drafts, to rewrite")
    parser.add_argument("--path", action="append", default=[], metavar="PREFIX",
                        help="only classes whose file starts with this (repeatable, or a comma list)")
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
    if name in ("bindings", "loaded_by"):
        return [resolve_class(graph, notes, t.strip()) for t in text.split(",") if t.strip()]
    if name in ("rules", "review"):
        return text.replace("\\n", "\n")  # `\n` in a set line starts a new line
    return clean_value(name, text)


def note_target(graph, notes, target):
    """`Name` -> node key, `A>B` or `A→B` -> edge key, `Name#function` -> function key."""
    kind, *ids = resolve_target(graph, notes, target)
    if kind == "edge":
        return edge_key(*ids)
    if kind == "function":
        return function_key(*ids)
    return node_key(ids[0])


def apply_set(graph, notes, tokens, by, ai=None):
    """One `target key=value ...` line; returns (note key, fields kept because they are yours, fields given)."""
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
        return key, notes.set(key, by, fields, ai, graph), fields
    except ValueError as error:
        raise AppError(str(error)) from error


def set_lines(args):
    if args.file:
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text("utf-8")
        return [shlex.split(line) for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    return [args.items]


STILL_TRUE_BATCH = 20  # more targets than this need --yes after the summary has been shown


def run_still_true(args, app, graph, notes):
    """`set --still-true Name ...`: the targets' notes are still right for today's code. Prints
    what is confirmed (target, field, the note text); a big batch needs --yes."""
    keys = [note_target(graph, notes, target) for target in args.items]
    shown = {key: [(name, notes.get(key, name)[0]) for name in FINGERPRINTED if notes.get(key, name)[0]]
             for key in keys}
    for line in textout.still_true_lines(shown):
        print(line)
    if len(keys) > STILL_TRUE_BATCH and not args.yes:
        sys.exit(f"error: {len(keys)} targets (more than {STILL_TRUE_BATCH}): read the list above, "
                 f"then repeat with --yes to confirm them all")
    for key in keys:
        notes.confirm(key, graph)
    app.save_notes(notes)


def run_set(args):
    app, graph, notes = open_app(args)
    graph = planned.hand_graph(graph, notes)  # planned and added-manually items are targets too
    if args.still_true:
        return run_still_true(args, app, graph, notes)
    by, ai = (args.by, False) if args.by else (AI_AUTHOR, True)
    results = [apply_set(graph, notes, tokens, by, ai) for tokens in set_lines(args)]
    app.save_notes(notes)
    print(f"set {len(results)} by {by}{' (AI draft)' if ai else ''}")
    for key, kept, _ in results:
        if kept:
            print(f"{key}: kept your {', '.join(kept)}")
    if ai:
        print_field_warnings(app, results)


def print_field_warnings(app, results):
    """Reminders (never a refusal) when an AI-written hint/description is long or code-like."""
    rules = field_rules.effective(app.data)
    warned = False
    for key, kept, fields in results:
        written = {name: text for name, text in fields.items() if name not in kept}
        for warning in field_rules.field_warnings(rules, written):
            print(f"reminder {key}: {warning}")
            warned = True
    if warned:
        print("\n".join(field_rules.reminder_lines(rules)))


SET_EPILOG = """Writing hint and description:
  They are for a new developer who is not very technical and does not know the code. Say what the
  class or connection is FOR and where it fits, not how it works (no types, function names or
  algorithms). hint = one short plain line (~80 chars). Details: `coderstanding fields <app>`."""


def run_fields(args):
    app = find_app(args.app, args.data)
    if args.reset or args.sets:
        sets = {}
        for token in args.sets or ():
            target, sep, text = token.partition("=")
            if not sep:
                raise AppError(f"not field.key=value: {token}")
            sets[target] = text
        try:
            field_rules.change(app.data, sets, args.reset, by=args.by or human_author(app.root))
        except ValueError as error:
            raise AppError(str(error)) from error
    print(field_rules.effective_text(field_rules.effective(app.data)))


def add_fields(commands):
    parser = commands.add_parser("fields", help="how to write each note field (hint, description, ...): "
                                 "the effective rules, your project's overrides marked")
    app_args(parser, view_options=False, worktree=False)
    parser.add_argument("--set", dest="sets", nargs="+", metavar="FIELD.KEY=VALUE",
                        help="override, e.g. hint.what=\"...\" hint.max=120 description.code_check=off "
                             "(keys: what who not good bad max code_check); house=\"...\" = house rules; "
                             "an empty value goes back to the built-in one")
    parser.add_argument("--reset", nargs="+", metavar="FIELD", help="drop overrides: a field name, house_rules or all")
    parser.add_argument("--by", metavar="NAME", help="record this editor (default: the human here)")
    parser.set_defaults(run=run_fields)


def plan_fields(args):
    """The --stage/--priority/... options of `plan --add` as note fields (unset ones left out)."""
    fields = {"hint": args.note, "stage": args.stage, "priority": args.priority,
              "importance": args.importance, "spec": args.spec, "where": args.where,
              "level": args.level, "tags": clean_value("tags", args.tags) if args.tags else None}
    return {name: value for name, value in fields.items() if value is not None}


def plan_target(graph, notes, text):
    """`Name` or `Class#function` of a hand-made or real item, as (id or Class#name)."""
    kind, *ids = resolve_target(graph, notes, text)
    if kind == "edge":
        raise AppError(f"{text}: give a class or Class#function, not a line")
    return FUNCTION_SEP.join(ids)


def plan_links(graph, notes, name, text):
    """`A>B,B>C` as (from, to) id pairs; the new item's own name stays as written."""
    pairs = []
    for token in (t.strip() for t in (text or "").split(",") if t.strip()):
        ends = token.replace("→", ">").split(">")
        if len(ends) != 2:
            raise AppError(f"not a line A>B: {token}")
        pairs.append(tuple(e.strip() if e.strip() == name else resolve_class(graph, notes, e.strip())
                           for e in ends))
    return pairs


def ask_text(app, name, on, ask):
    """Why `plan --add` stopped: the name matches real code; how to link to it or add anyway."""
    owner = f"{on}#" if on else ""
    found = ", ".join(f"{owner}{n}" for n in ask["exact"] + ask["close"])
    kind = "is" if ask["exact"] else "looks like"
    return (f"{owner}{name} {kind} real code: {found}\n  put notes on the real one instead: "
            f"coderstanding set {app} <target> hint=\"...\"\n  or add it anyway: repeat with --anyway")


def run_plan(args):
    """`plan <app>`: list what is not built; --add / --drop / --link / --keep-both / --accept."""
    app, graph, notes = open_app(args)
    hand = planned.hand_graph(graph, notes)
    by, ai = (args.by, False) if args.by else (AI_AUTHOR, True)
    try:
        if args.add:
            on = resolve_class(hand, notes, args.on) if args.on else None
            if args.spec and planned.spec_stage(notes, args.spec) is None:
                raise AppError(f"no spec {args.spec} (see `coderstanding specs {app.name}`)")
            result = planned.add_item(hand, notes, args.add, by, ai, on=on, made=args.kind,
                                      fields=plan_fields(args), anyway=args.anyway,
                                      links=plan_links(hand, notes, args.add.strip(), args.links))
            if "ask" in result:
                raise AppError(ask_text(app.name, args.add.strip(), on, result["ask"]))
            print(f"added [{args.kind}] {result['key'][len('node:'):]}" + (" (AI draft)" if ai else ""))
        elif args.drop:
            target = plan_target(hand, notes, args.drop)
            print("dropped " + ", ".join(planned.drop_item(notes, node_key(target))))
        elif args.link:
            old, new = (plan_target(hand, notes, t) for t in args.link)
            moved = planned.link_item(hand, notes, old, new)
            print(f"linked {old} -> {new} ({len(moved)} notes keys moved)")
        elif args.keep_both:
            moved = planned.keep_both(notes, plan_target(hand, notes, args.keep_both))
            print(f"renamed {moved[0][0]} -> {moved[0][1]}")
        elif args.accept:
            keys = planned.accept_item(notes, node_key(plan_target(hand, notes, args.accept)),
                                       args.by or human_author(app.root))
            print("accepted " + ", ".join(keys))
        else:
            print(textout.format_plan(app.name, planned.plan_rows(graph, notes)))
            return
    except ValueError as error:
        raise AppError(str(error)) from error
    app.save_notes(notes)


def add_plan(commands):
    parser = commands.add_parser("plan", help="planned and added-manually items and stub functions: "
                                 "list them (default) or add, drop, link, accept")
    app_args(parser, view_options=False)
    parser.add_argument("--add", metavar="NAME", help="add a planned class (or with --on, a planned function)")
    parser.add_argument("--on", metavar="CLASS", help="--add: the class the planned function goes on")
    parser.add_argument("--kind", choices=planned.MADE_KINDS, default=planned.PLANNED,
                        help="--add: planned (not in the code yet, default) or manual (real, the scan can't see it)")
    parser.add_argument("--note", help="--add: what it is for, in plain words (the hint)")
    parser.add_argument("--stage", choices=SPEC_STAGES, help="--add: its own stage (default: its spec's)")
    parser.add_argument("--priority", choices=PRIORITIES, help="--add: work priority (how soon)")
    parser.add_argument("--importance", choices=PRIORITIES, help="--add: how much it matters")
    parser.add_argument("--spec", metavar="ID", help="--add: the spec it belongs to")
    parser.add_argument("--where", help="--add --kind manual: the file, folder or service it lives in")
    parser.add_argument("--tags", help="--add: a,b")
    parser.add_argument("--level", type=int, help="--add: the map level it shows at (default 1)")
    parser.add_argument("--links", metavar="A>B,...", help="--add: lines to or from the new class")
    parser.add_argument("--anyway", action="store_true", help="--add: even when the name matches real code")
    parser.add_argument("--drop", metavar="TARGET", help="remove a planned or added item (a class takes its lines)")
    parser.add_argument("--link", nargs=2, metavar=("PLANNED", "REAL"),
                        help="the planned class or Class#function is built as REAL: move its notes there")
    parser.add_argument("--keep-both", metavar="NAME", help="a planned class whose name is real now: rename it NAME (planned)")
    parser.add_argument("--accept", metavar="TARGET", help="make an AI-drafted item yours")
    parser.add_argument("--by", metavar="NAME", help=f"write as this person (default: AI draft by {AI_AUTHOR})")
    parser.set_defaults(run=run_plan)


def add_set(commands):
    parser = commands.add_parser("set", help="set notes: target key=value ...",
                                 epilog=SET_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    app_args(parser, view_options=False)
    parser.add_argument("items", nargs="*", help="Name|A>B key=value ...")
    parser.add_argument("--file", help="batch file, one `target key=value ...` per line; - = stdin")
    parser.add_argument("--by", metavar="NAME",
                        help=f"write as this person (default: AI draft by {AI_AUTHOR})")
    parser.add_argument("--still-true", action="store_true",
                        help="items are targets (Name, A>B, Name#fn): keep their notes, mark them "
                             "up to date with the code (clears the stale mark)")
    parser.add_argument("--yes", action="store_true",
                        help=f"with --still-true: confirm more than {STILL_TRUE_BATCH} targets at once")
    parser.set_defaults(run=run_set)


def run_serve(args):
    from .server import serve, systemd_socket
    try:
        app = find_app(args.app, args.data) if args.app else None
    except AppError as error:
        sys.exit(str(error))
    from .helper import enter_service_mode, helper_running, log_line, report_service_failure
    passed = systemd_socket()  # socket activation: systemd owns the port, so no second-helper check
    options = {"allow_origin": args.allow_origin, "idle_exit": args.idle_exit, "sock": passed,
               "tray": args.tray}
    if passed is None and helper_running(args.port):  # before service mode: it must not take over the running one's pid file
        message = f"a helper is already running on http://127.0.0.1:{args.port}/"
        print(message)
        if args.service:
            log_line(message)
        return
    if not args.service:
        return serve(app, args.port, **options)
    console = enter_service_mode()
    try:
        serve(app, args.port, **options)
    except Exception as error:  # nobody watches the log: say why, then fail
        report_service_failure(error, console)
        sys.exit(1)


def add_serve(commands):
    parser = commands.add_parser("serve", help="open the viewer on localhost: every registered "
                                 "app, starting on <app> if given")
    app_args(parser, view_options=False, optional=True, worktree=False)
    parser.add_argument("--port", type=int, default=8517, help="port (default 8517)")
    parser.add_argument("--allow-origin", metavar="URL",
                        help="hosted page origin that may use this server as its helper (default: env "
                             "CODERSTANDING_ALLOW_ORIGIN, then \"origin\" in "
                             "~/.config/coderstanding/hosted.json)")
    parser.add_argument("--service", action="store_true",
                        help="run as the helper without a console (Windows): log to the "
                             "helper log file and record the pid")
    parser.add_argument("--idle-exit", type=float, metavar="SECONDS",
                        help="exit once no page has used the server for SECONDS (also when none "
                             "comes after the start); never during a scan or a running request")
    parser.add_argument("--tray", action="store_true",
                        help="show the tray icon while running (when Qt is installed)")
    parser.set_defaults(run=run_serve)


def run_rescan(args):
    app = worktree_app(find_app(args.app, args.data), args.repo)
    if args.reset_level_names and not args.relevel:
        raise AppError("--reset-level-names only goes with --relevel")
    graph = rescan(app, key_values(args.setting))
    print(f"{app.name}: {len(graph.nodes)} nodes, {len(graph.edges)} edges -> {app.graph_path}")
    stale = stale_repo_cache(app)
    if stale:
        print(f"warning: {stale} only holds an old generated cache (the cache now lives in "
              f"{app.cache_dir.parent}); nothing reads it, delete it by hand", file=sys.stderr)
    if args.relevel:
        done = relevel(app, args.relevel, args.reset_level_names)
        print(f"relevel {args.relevel}: cleared {done['cleared']} level notes"
              + (f", backup {done['backup']}" if done["backup"] else "")
              + (", level names reset" if args.reset_level_names else ", level names kept"))


def add_rescan(commands):
    parser = commands.add_parser("rescan", help="scan an app now into its data folder "
                                 "(what the viewer's Rescan runs, niced)")
    app_args(parser, view_options=False)
    parser.add_argument("--setting", action="append", default=[], metavar="KEY=VALUE",
                        help=f"a scan setting for this run only, never saved ({', '.join(SETTINGS)}); repeatable")
    parser.add_argument("--relevel", nargs="?", const="ai", choices=RELEVEL_SCOPES,
                        help="opt-in: guess every level again and clear the per-class level notes so the "
                        "guess shows (ai = only AI-drafted ones, the default; all = yours too). The cleared "
                        "notes are backed up in the data folder; level names are kept")
    parser.add_argument("--reset-level-names", action="store_true",
                        help="with --relevel: also drop the saved level count and names")
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
    from .voices import Voices
    print(privacy_report(HOST, port, origin, source, helper_listening(HOST, port), Voices().summary()))


def add_privacy(commands):
    parser = commands.add_parser("privacy", help="show that Coderstanding works fully offline: "
                                 "network guard, bind address, allowed origin, voice policy and the "
                                 "built-in voices folder (drop Piper voices there)")
    parser.add_argument("--port", type=int, help="the helper's port (default 8517)")
    parser.set_defaults(run=run_privacy)


def version():
    try:
        return importlib.metadata.version("coderstanding")
    except importlib.metadata.PackageNotFoundError:
        return "(source checkout)"


class HintParser(argparse.ArgumentParser):
    """Says what to type when a command is missing its app or targets, instead of the bare
    argparse error (subcommand parsers use this class too)."""

    def error(self, message):
        if "required" in message and " " in self.prog:
            names = [a.metavar or a.dest for a in self._get_positional_actions()]
            typed = " ".join(f"<{n}>" + ("..." if a.nargs in ("+", "*") else "")
                             for n, a in zip(names, self._get_positional_actions()))
            apps = ", ".join(sorted(load_registry())) or "none yet (coderstanding add <folder>)"
            self.exit(2, f"error: {message}\ntype: {self.prog} {typed} [options]   "
                         f"(registered apps: {apps})\n")
        super().error(message)


def build_parser():
    parser = HintParser(prog="coderstanding")
    parser.add_argument("--version", action="version", version=f"coderstanding {version()}")
    commands = parser.add_subparsers(dest="command", required=True)
    for add in (add_scan, add_add, add_settings, add_graph, add_zoom, add_node, add_view,
                add_specs, add_memos, add_tours, add_tour, add_comment, add_outline, add_setup, add_read, add_ref, add_find, add_impact,
                add_uses, add_env, add_dupes, add_hotspots, add_progress,
                add_brief, add_tests,
                add_warnings, add_diagnose, add_rules, add_curate, add_plan, add_set, add_fields, add_serve, add_rescan,
                add_remove, add_rename, add_privacy, *SETUP_COMMANDS):
        add(commands)
    return parser


def print_other_code_hint():
    """The worktree hint, once per checkout and running copy of the code (marker in the data home),
    so piped commands stay clean; it shows again when another copy of the code runs."""
    running = Path(__file__).resolve().parents[1]
    hint = worktrees.other_code_hint(running)
    checkout = worktrees.checkout_top(Path.cwd())
    if hint and worktrees.changed_since_shown(data_home(), f"other-code:{checkout}", str(running)):
        print(hint, file=sys.stderr)


def main(argv=None):
    install_offline_guard()  # before anything else runs (DESIGN 18)
    print_other_code_hint()
    args = build_parser().parse_args(argv)
    try:
        args.run(args)
    except AppError as error:
        sys.exit(f"error: {error}")


if __name__ == "__main__":
    main()
