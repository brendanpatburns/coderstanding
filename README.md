# Coderstanding

Scan a code base, see its classes and how they connect, at the level of detail you choose.
A local viewer draws the graph (granularity slider, concrete-only view, notes on classes and
connections, guided code tours), and a slim text CLI prints the same views for AI coding agents,
so they read structure instead of grepping files.

## Languages

| Language | What is read |
|---|---|
| C# | types, bases, fields, calls; Godot C# idioms (signals, `[GlobalClass]`) |
| GDScript + Godot | scripts, inner classes, `preload` names, signals and emits, `project.godot` autoloads, `.tscn` scenes |
| Python | modules, classes, bases, imports, calls, type hints |
| JavaScript / TypeScript | modules, classes, extends/implements, imports/require, calls, events |
| C / C++ | header/source pairs, namespaced types, includes, free functions |
| HTML | pages, `<script src>` / `<link href>` imports |

Stack and outside dependencies come from project files (`package.json`, `pyproject.toml`,
`requirements*.txt`, `.csproj`, Dockerfiles, compose files, git submodules).

## Install

Needs Python 3.10 or newer (Linux, macOS or Windows). No source checkout is needed.

### From a release

```bash
pipx install coderstanding-0.2.2-py3-none-any.whl      # or: pip install --user coderstanding-0.2.2-py3-none-any.whl
coderstanding --version
```

On Windows, install pipx first, then the wheel:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath          # then open a NEW terminal, or `pipx` is not found
pipx install coderstanding-0.2.2-py3-none-any.whl
coderstanding helper install   # starts the helper now and checks it answers
```

If `pipx` is still not found, `py -m pipx install coderstanding-0.2.2-py3-none-any.whl` works without
any PATH change.

Installing downloads the tree-sitter grammar wheels from PyPI once (the only network use, at install
time; Coderstanding itself never sends your code anywhere). To install on a machine without access to
PyPI, download everything on another machine with the same OS and Python version, copy the folder over,
and install from it:

```bash
pip download coderstanding-0.2.2-py3-none-any.whl -d wheels/     # on a machine with internet
pip install --no-index --find-links wheels/ coderstanding-0.2.2-py3-none-any.whl   # on the target
```

(`pipx install --pip-args="--no-index --find-links wheels/" coderstanding-0.2.2-py3-none-any.whl` for pipx.)

### Helper at login and tray icon

The viewer is served by a small local helper. To start it at every login:

```bash
coderstanding helper install     # Linux: systemd user units. Windows: Startup entries (no admin needed)
coderstanding helper start       # start again later; also: stop, restart, status, log, uninstall
coderstanding add <name> <folder>   # register a project (or use Projects... -> Add project... in the viewer)
```

`helper install` also starts the helper (`--no-start` to skip) and `helper install`/`helper start` check
that it answers on http://127.0.0.1:8517/, printing the last log lines and the log path if not. The
helper starts fine with no project registered; the page then shows how to add one.
Add `--dry-run` to see what it would write. The optional tray icon (green = up, amber = scanning,
grey = down; start/stop/log menu) needs Qt: `pipx install "coderstanding-0.2.2-py3-none-any.whl[tray]"` or
`pipx inject coderstanding PySide6` (pipx) or `python -m pip install "coderstanding[tray]"`, then
`coderstanding helper install` again. On Windows the log is
`%LOCALAPPDATA%\coderstanding\helper.log`; on Linux use `journalctl --user -u coderstanding-helper`.

### From source (development)

```bash
git clone <this repo> coderstanding && cd coderstanding
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Fedora needs python3-devel
bin/coderstanding scan <path>        # runs from the checkout without installing
scripts/build-release.sh             # wheel + sdist into dist/, then a fresh-venv smoke test
```

## Use

```bash
coderstanding add myapp "/path/to/repo" --data ~/.local/share/coderstanding/myapp
coderstanding graph myapp 1          # top level as slim text (scans on first use)
coderstanding node myapp Simulation  # one class: neighbours, file, tests, rules
coderstanding zoom myapp A B         # what runs between A and B
coderstanding serve [myapp]          # viewer at http://127.0.0.1:8517/ for every registered app (opens on myapp)
coderstanding rescan myapp           # scan now into its data folder (what the viewer's Refresh runs, niced)
```

The viewer's app picker (toolbar, or `[` / `]`) switches between registered projects, refreshes one, and
"Projects… → Add project…" registers and scans a new repo (data in `~/.local/share/coderstanding/<name>`).

`--data` keeps graph, notes and layout out of the repo; leave it out to use `<repo>/.coderstanding/`.
Registered apps live in `~/.config/coderstanding/apps.json`. The scan reruns by itself when files
change. `coderstanding <command> --help` lists every option.

| Command | What it gives |
|---|---|
| `graph <app> [level]` | classes and connections at a level (0 = domains) |
| `node <app> <name> [--functions]` | one class, or who calls what inside it |
| `zoom <app> <from> <to>` | one connection, one level deeper |
| `view <app> ...` | what the viewer shows (the viewer's "Copy for AI") |
| `tests <app> <name> [--deep]` | test files to run after changing a class |
| `warnings <app>` | unused classes, god classes, cycles, unresolved refs |
| `curate <app>` / `set <app> ...` | fill in levels, domains, hints, rules |
| `rules <app> [add "<glob>" ignore=.. \| remove N]` | ignore warning kinds by folder (and lang/kind), no rescan |
| `settings <app>` | scan settings (hub threshold) |
| `rename <old> <new>` / `remove <app> [--delete-data] [--delete-notes]` | registry upkeep (also the viewer's Projects… modal) |

## Using it with AI coding agents

Any agent that can run shell commands can use Coderstanding (Claude Code, GitHub Copilot, Cursor,
others). Everything runs locally and offline.

1. Install it (above), then register the repo: `coderstanding add myapp "C:\code\myapp"`
2. Tell the agent about it. Run `coderstanding guide --snippet --app myapp` to see the block, or write
   it into the file your tool reads (re-running replaces the block, never duplicates it):

| Tool | Command |
|---|---|
| Claude Code | `coderstanding skill install` and `coderstanding hook` (merge the JSON into `~/.claude/settings.json`); or `coderstanding guide --write CLAUDE.md --app myapp` |
| GitHub Copilot | `coderstanding guide --write .github/copilot-instructions.md --app myapp` |
| Cursor | `coderstanding guide --write .cursor/rules/coderstanding.mdc --app myapp` (or `.cursorrules`) |
| Others (Codex, Aider, ...) | `coderstanding guide --write AGENTS.md --app myapp` |

3. The agent can learn the whole tool with `coderstanding guide` (the full reference, shipped in the
   package). An example session:

```bash
coderstanding graph myapp 1                 # the top level, as short text
coderstanding find myapp checkout           # where does checkout live?
coderstanding outline myapp Cart            # its functions with file:first-last lines
coderstanding impact myapp Cart#add         # who depends on it, tests to rerun
coderstanding tour myapp checkout-flow      # a guided walk through a flow
coderstanding warnings myapp                # unused classes, cycles, unsure lines
```

Privacy: Coderstanding makes no outside network calls and sends no code anywhere; see
[Privacy](#privacy-works-fully-offline). The agent's own tool still talks to its vendor as usual.

## Hosted page (optional)

The viewer can also be served as static files from your own server, behind your own login proxy
(for example oauth2-proxy in front of a small web server holding `viewer/` plus a `hosted.json`
copied from `deploy/hosted.json`). It holds no maps and runs no Coderstanding code: on each PC the
page talks to the local helper (`coderstanding serve`, started at login by
`coderstanding helper install`), so every map stays on that PC. Without the helper it shows how to
start it. Tell the helper which page may use it, per run or once in your config:

```bash
coderstanding serve --allow-origin https://code.your-server.example
echo '{"origin": "https://code.your-server.example"}' > ~/.config/coderstanding/hosted.json
```

The helper binds 127.0.0.1 only, answers cross-origin requests from that one origin, and
refuses writes from any other site.

## Privacy: works fully offline

Coderstanding makes no outside network calls, so it is safe to point at company code.

- **Guaranteed**: scans, maps, notes and tours stay on your PC. The only network traffic is
  between your browser and the local server on `127.0.0.1`.
- **How it is enforced**: every entry point (CLI, `serve`, background scans, tray, AI hook) turns
  on a Python audit hook that refuses any connection, send or name lookup that is not loopback,
  with an error naming the destination. The server listens on 127.0.0.1 only and has no option
  to listen on a network address. Every page it serves carries a strict Content-Security-Policy,
  so the browser itself refuses any request to another site. `git` runs only local commands with
  its network transports turned off. Tests check all of this, plus a scan of the source for
  network APIs and a browser run that records every request the page makes.
- **Check a machine**: `coderstanding privacy` prints the guard state, bind address, allowed
  origin and voice policy. The tests: `pytest tests/test_offline.py` and `node tests/e2e/run.mjs`.
- **What to avoid**: the hosted page (above) is the one deliberate exception. Leave
  `~/.config/coderstanding/hosted.json` absent on a work machine (the default), and no other
  site can call the helper. Tour narration uses only on-device voices (`localService`): online
  voices, such as Chrome's "Google …" voices or Edge's "Microsoft … Online (Natural)" voices,
  would send the text to their vendor and are never used. Without a local voice, tours play
  silently on their timeline. On Windows, the built-in voices are local.

## More

- For AI agents: `coderstanding guide` (source: `docs/AI_GUIDE.md`; how to read the slim text, curating)
- Design: `docs/DESIGN.md` · components: `docs/COMPONENTS.md` · small planned items: `docs/TODO.md`

## License

MIT, see `LICENSE`.
