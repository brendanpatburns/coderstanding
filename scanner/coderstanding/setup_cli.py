"""Commands that set Coderstanding up on a PC (not about a scanned app): `helper`, `tray`, `hook`,
`hook-run`, `skill`. Same shape as cli.py: one add_* function per command."""

import sys
from argparse import BooleanOptionalAction

from . import helper, hook, skill


def run_helper(args):
    if args.action == "install":
        helper.install(tray=args.tray, dry_run=args.dry_run)
    elif args.action == "uninstall":
        helper.uninstall(dry_run=args.dry_run)
    elif args.action == "status":
        helper.status()
    elif args.action == "log":
        helper.show_log()
    else:
        helper.control(args.action)


def add_helper(commands):
    parser = commands.add_parser("helper", help="run the local helper at login (systemd user unit on "
                                 "Linux, Startup entry on Windows)")
    parser.add_argument("action", choices=["install", "uninstall", "status", "start", "stop",
                                           "restart", "log"])
    parser.add_argument("--dry-run", action="store_true", help="install/uninstall: show, change nothing")
    parser.add_argument("--tray", action=BooleanOptionalAction, default=None,
                        help="install: with or without the tray icon (default: with when Qt is installed)")
    parser.set_defaults(run=run_helper)


def run_tray(args):
    try:
        from . import tray
    except ImportError:
        sys.exit('the tray needs Qt: pip install "coderstanding[tray]"')
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


SETUP_COMMANDS = (add_helper, add_tray, add_hook, add_hook_run, add_skill)
