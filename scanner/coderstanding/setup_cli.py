"""Commands that set Coderstanding up on a PC (not about a scanned app): `helper`, `tray`, `hook`,
`hook-run`, `skill`, `guide`. Same shape as cli.py: one add_* function per command."""

import sys
from argparse import BooleanOptionalAction

from . import guide, helper, hook, skill


def run_helper(args):
    if args.action == "install":
        ok = helper.install(tray=args.tray, dry_run=args.dry_run, start=args.start)
        if not ok:
            sys.exit(1)
    elif args.action == "uninstall":
        helper.uninstall(dry_run=args.dry_run)
    elif args.action == "status":
        helper.status()
    elif args.action == "log":
        helper.show_log()
    elif not helper.control(args.action, verify=True):
        sys.exit(1)


def add_helper(commands):
    parser = commands.add_parser("helper", help="run the local helper at login (systemd user unit on "
                                 "Linux, Startup entry on Windows)")
    parser.add_argument("action", choices=["install", "uninstall", "status", "start", "stop",
                                           "restart", "log"])
    parser.add_argument("--dry-run", action="store_true", help="install/uninstall: show, change nothing")
    parser.add_argument("--no-start", dest="start", action="store_false",
                        help="install: do not start the helper now")
    parser.add_argument("--tray", action=BooleanOptionalAction, default=None,
                        help="install: with or without the tray icon (default: with when Qt is installed)")
    parser.set_defaults(run=run_helper)


def run_tray(args):
    try:
        from . import tray
    except ImportError:
        sys.exit(f"the tray needs Qt: {helper.tray_install_hint()}")
    tray.main()


def add_tray(commands):
    parser = commands.add_parser("tray", help="show the helper's tray icon (needs coderstanding[tray])")
    parser.set_defaults(run=run_tray)


def run_hook(args):
    print(hook.settings_snippet())


def add_hook(commands):
    parser = commands.add_parser("hook", help="print the Claude Code hooks snippet for "
                                 "~/.claude/settings.json (merge it with your existing hooks)")
    parser.set_defaults(run=run_hook)


def run_hook_run(args):
    sys.exit(hook.main())


def add_hook_run(commands):
    parser = commands.add_parser("hook-run", help="what the hook calls: reads the event on stdin "
                                 "(not for typing by hand)")
    parser.set_defaults(run=run_hook_run)


def run_skill(args):
    done = skill.install(args.dest, yes=args.yes)
    print(f"installed the skill to {done}" if done else "left it as it was")


def add_skill(commands):
    parser = commands.add_parser("skill", help="install the Claude Code skill")
    parser.add_argument("action", choices=["install"])
    parser.add_argument("--dest", metavar="DIR", help="skill folder (default ~/.claude/skills/coderstanding)")
    parser.add_argument("-y", "--yes", action="store_true", help="replace an existing skill without asking")
    parser.set_defaults(run=run_skill)


def run_guide(args):
    if args.write:
        print(f"{guide.write_snippet(args.write, args.app)} {args.write}")
    elif args.snippet:
        print(guide.snippet(args.app), end="")
    else:
        print(guide.guide_text(), end="")


def add_guide(commands):
    parser = commands.add_parser("guide", help="print the guide for AI coding agents (any tool); "
                                 "--snippet / --write FILE: the short block for its instructions")
    parser.add_argument("--snippet", action="store_true", help="print the short block to paste into "
                        "AGENTS.md, CLAUDE.md, .github/copilot-instructions.md or a Cursor rule")
    parser.add_argument("--write", metavar="FILE", help="add the block to FILE (created if missing; "
                        "a re-run replaces its own block)")
    parser.add_argument("--app", metavar="NAME", help="app name for the snippet (default <app>)")
    parser.set_defaults(run=run_guide)


SETUP_COMMANDS = (add_helper, add_tray, add_hook, add_hook_run, add_skill, add_guide)
