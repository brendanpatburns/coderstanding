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
  Godot, Python, JS/TS)
- `scanner/coderstanding/views.py` view engine: collapse, concrete view, zoom (the only place for it)
- `scanner/coderstanding/textout.py` slim text format for Claude
- `scanner/coderstanding/stack.py` stack reader: tech stack + external deps
- `scanner/coderstanding/specs.py` spec store: read/write/search specs in `.coderstanding/specs/`
- `scanner/coderstanding/items.py` shared Markdown item store; `tours.py` tours in `.coderstanding/tours/` (DESIGN 7c)
- `viewer/` static HTML/JS, draws what the engine returns
- `tests/` pytest + fixture repos

## Merge / push / build / deploy
- Work on a branch, run the tests for what you changed, then open a pull request.

## Dev setup
- Python venv at `.venv/` (gitignored): `.venv/bin/pip install -e ".[dev]"` once `pyproject.toml`
  exists. Needs Fedora `python3-devel` to build tree-sitter on Python 3.15.
- Tests: `.venv/bin/pytest tests/<file>` for your own work; the full suite runs after merges.
  Viewer JS tests: `node --test tests/js/test_layout.mjs tests/js/test_badges.mjs` (no npm needed).
  Viewer end-to-end checks in headless Chromium (Node 22 + `chromium-browser`, no packages):
  `node tests/e2e/run.mjs` (own server on a spare port, fixture `tests/e2e/fixture`, failure shots
  in `~/Downloads/coderstanding-wip/qa-fail-*.png`); add `--app <repo> --app-data <scratch dir>`
  to also check a real app's layout (read-only). Light: run it after viewer changes.
- Run without installing: `bin/coderstanding scan <path>` (uses `.venv/bin/python`).
- Viewer (UI wording: "projects", "Projects…", "Add project…", "Refresh"; code/API/#hash keep "app"/"scan"): `bin/coderstanding serve [<app>]` (registered name or repo path; no app = first registered; one server shows every registered app, `#<app>` in the URL, `[`/`]` flick; API takes `?app=`; the local serve is also the hosted page's helper: the hosted page sends every API call to it, CORS for that one origin) then open http://127.0.0.1:8517/ (`--data DIR` keeps
  graph, notes, layout and cache out of a repo you only read). URL options for checks: `?level=3&theme=dark&mode=edit&tab=stack&select=<id>&zoom=<id>&expand=<id>&layout=layered|organic|hub|grid` (Layered is the default). Tests/e2e isolate the registry with env `CODERSTANDING_REGISTRY` and `CODERSTANDING_DATA_HOME`.
- Helper at login: `scripts/install-helper.sh` renders `deploy/systemd/coderstanding-{helper,tray}.service` into `~/.config/systemd/user` and enables them (run once; re-run after moving the repo). Control: `systemctl --user start|stop|restart|status coderstanding-helper`; logs: `journalctl --user -u coderstanding-helper -f` (or tray menu "Show log"). The tray (`scripts/helper_tray.py`, system python3 + PyQt6/PySide6) shows green = up, amber = syncing, grey = down. Only one server can own port 8517: stop a hand-started one before starting the unit.
