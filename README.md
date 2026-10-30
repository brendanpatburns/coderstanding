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

## Install (once)

Needs Python 3.15+ (and its development headers to build tree-sitter, e.g. Fedora `python3-devel`).

```bash
git clone <this repo> coderstanding && cd coderstanding
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Fedora needs python3-devel
ln -sfn "$PWD/bin/coderstanding" ~/.local/bin/coderstanding   # puts it on PATH
mkdir -p ~/.claude/skills && ln -sfn "$PWD/docs/skill" ~/.claude/skills/coderstanding  # Claude skill
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

## Hosted page (optional)

The viewer can also be served as static files from your own server, behind your own login proxy
(for example oauth2-proxy in front of a small web server holding `viewer/` plus a `hosted.json`
copied from `deploy/hosted.json`). It holds no maps and runs no Coderstanding code: on each PC the
page talks to the local helper (`coderstanding serve`, started at login by
`scripts/install-helper.sh`), so every map stays on that PC. Without the helper it shows how to
start it. Tell the helper which page may use it, per run or once in your config:

```bash
coderstanding serve --allow-origin https://code.your-server.example
echo '{"origin": "https://code.your-server.example"}' > ~/.config/coderstanding/hosted.json
```

The helper binds 127.0.0.1 only, answers cross-origin requests from that one origin, and
refuses writes from any other site.

## More

- For Claude and agents: `docs/FOR_CLAUDE.md` (how to read the slim text, curating)
- Design: `docs/DESIGN.md` · components: `docs/COMPONENTS.md` · small planned items: `docs/TODO.md`

## License

MIT, see `LICENSE`.
