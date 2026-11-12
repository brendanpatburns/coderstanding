# Coderstanding

Local tool that scans a code base (C# and Godot first) and shows its classes and connections
as a directed graph with a granularity slider, two views (all types / concrete only), and
user-written text on connections.

- Design: `docs/DESIGN.md` (read first)
- Components: `docs/COMPONENTS.md` (read before adding anything; extend before adding)
- Agent rules: `docs/AGENTS.md`

## Layout (planned)
- `scanner/coderstanding/` Python package `coderstanding`: CLI, model, resolver, file walk,
  level guesser; `scanner/coderstanding/languages/` one module per language (C#, GDScript +
  Godot, Python, JS/TS, Kotlin, shell)
- `scanner/coderstanding/views.py` view engine: collapse, concrete view, zoom (the only place for it)
- `scanner/coderstanding/textout.py` slim text format for Claude
- `scanner/coderstanding/guide.py` + `docs/AI_GUIDE.md` the tool-neutral AI guide (`coderstanding guide`) and the snippet for agent instruction files
- `scanner/coderstanding/stack.py` stack reader: tech stack + external deps
- `scanner/coderstanding/cards.py` card store: specs and memos (the viewer's Notes) in the app's data folder, `specs/` and `memos/` (the repo's `.coderstanding/` unless the app keeps its data elsewhere)
- `scanner/coderstanding/items.py` shared Markdown item store; `tours.py` tours in `.coderstanding/tours/` (DESIGN 7c)
- `viewer/` static HTML/JS, draws what the engine returns
- `tests/` pytest + fixture repos

## Merge / push / build / deploy
- Work on a branch, run the tests for what you changed, then open a pull request.

## Dev setup
- Python venv at `.venv/` (gitignored): `.venv/bin/pip install -e ".[dev]"` once `pyproject.toml`
  exists. Needs Fedora `python3-devel` to build tree-sitter on Python 3.15.
- Tests: `.venv/bin/pytest <files>` for your own work: the files `coderstanding tests coderstanding --diff` lists for your change (not only new tests); the full suite runs after merges.
  Viewer JS tests: `node --test tests/js/test_layout.mjs tests/js/test_badges.mjs` (no npm needed).
  Viewer end-to-end checks in headless Chromium (Node 22 + `chromium-browser`, no packages):
  `node tests/e2e/run.mjs` (`--only a,b` runs just the checks whose name contains a or b (each check resets the page first and builds its own setup, so any check runs alone; `--step <exact name part>` for one name with commas); silent: real speech only with `CODERSTANDING_E2E_SPEECH=1`, never on the user's desktop unless asked; own server on a spare port, fixture `tests/e2e/fixture`, failure shots
  in `~/Downloads/coderstanding-wip/qa-fail-*.png`); add `--app <repo> --app-data <scratch dir>`
  to also check a real app's layout (read-only). Light: run it after viewer changes.
  Layout numbers without a browser (before/after a layout change): `nice -n 19 node tests/e2e/layout_bench.mjs --app <repo> --app-data <scratch dir> [--levels 1,2,5] [--split] [--layout <other layout.js>] [--boxtext name|short|full]`.
- Run without installing: `bin/coderstanding scan <path>` (uses `.venv/bin/python`).
- Viewer (UI wording: "projects", "Projects…", "Add project…", "Refresh"; code/API/#hash keep "app"/"scan"): `bin/coderstanding serve [<app>]` (registered name or repo path; no app = first registered; one server shows every registered app, `#<app>` in the URL, `[`/`]` flick; API takes `?app=`; the local serve is also the hosted page's helper: the hosted page sends every API call to it, CORS for that one origin) then open http://127.0.0.1:8517/ (`--data DIR` keeps
  graph, notes, layout and cache out of a repo you only read). URL options for checks: `?level=3&theme=dark&mode=edit&tab=stack&select=<id>&zoom=<id>&expand=<id>&layout=layered|organic|hub|grid&domains=1&between=every&domain=<name>&selfcalls=0&boxtext=name|short|full&lines=explained|all&preset=read|explore|edit&more=open|closed&planned=hide|show|only&track=<progress track>&hide_type=.tscn&hide_file_kind=Data&ref=<location ref, with &app=>&hl=A-B&file=<path>&comment=<id>` (`ref=` is resolved by `/api/ref` into the other options; `hl` lights lines on a tour step, `file`+`hl` show a banner with source links, `comment` reveals a comment; Layered is the default). Every view switch (its URL name, values, default, what remembers it, which a tour may set) is one row of the table in `viewer/view_options.js`; precedence: URL > tour step > tour > remembered/preset > default; URL options are never saved. The default preset is Read; the e2e `open()` pins `preset=explore&more=open`. Tests/e2e isolate the registry with env `CODERSTANDING_REGISTRY` and `CODERSTANDING_DATA_HOME`.
- Helper on demand (nothing runs at login): `scripts/install-helper.sh` (= `bin/coderstanding helper install`) writes, pointing at the `coderstanding` command, the systemd user `coderstanding-helper.socket` (enabled; 127.0.0.1:8517) + the `.service` it activates and a `coderstanding://` link handler (`.desktop` + `xdg-mime`) on Linux, the HKCU `coderstanding://` link handler (`reg add`) on Windows; it removes older login entries (helper and tray). `helper.py` is the only place that knows their contents; a checkout without an installed entry point gets `PYTHONPATH` set. Every started helper runs `serve --idle-exit 10 --tray`: it quits 10 s after the last page call (the page's 3 s heartbeat `/api/alive`; requests with `X-Coderstanding-Monitor` (tray, CLI checks) do not count; never during a scan or a running request) and shows the tray (needs `pip install ".[tray]"`) as a child that quits when its stdin closes. Linux `helper stop` closes the socket too (off until start, the link, or next login). The page's Start helper button opens `coderstanding://start` (`helper url-start`, ignores the URL). Re-run install after moving the repo or the venv. Control: `coderstanding helper start|stop|restart|status|log|uninstall`; Linux logs: `journalctl --user -u coderstanding-helper -f`, Windows: `%LOCALAPPDATA%\coderstanding\helper.log`. A hand-started `serve` (no `--idle-exit`) runs until stopped; only one server can own port 8517.
- Dev vs installed: a checkout reads `viewer/` and `docs/` from the repo; a wheel carries them as package data (`coderstanding/viewer`, `coderstanding/docs`) and `assets.py` picks whichever exists. Nothing at runtime may name a repo path; ask `assets`, `userconfig` (per-platform config/data/log folders) or `helper` instead. Pitfall: keep viewer files flat (new subfolders need an entry in `pyproject.toml` `packages`/`package-data`). Release build: `scripts/build-release.sh` (wheel + sdist in `dist/`, fresh-venv smoke test); publishing a release only when the user asks. Python floor is 3.10 (tested 3.10, 3.11, 3.12, 3.15; `tomli` backs `tomllib` on 3.10); Windows is untested here.
