# Changelog

## 0.2.2 (2026-10-30)

- Fixes #2 (Windows helper):
  - No more console windows: every child process (git, the rescan child, taskkill, schtasks)
    starts through one helper (`procs.py`) that hides the window on Windows.
  - Refresh is manual only: the server and the CLI never rescan by themselves, so `git ls-files`
    no longer runs on every request. A project is scanned by Refresh, `coderstanding rescan <app>`
    or `add` (first scan; `--no-scan` skips it); an unscanned project says "press Refresh". Commands read
    the saved graph, also one an older scanner wrote (noted as "made by an older scanner").
  - One helper only: the server binds its port exclusively (`SO_EXCLUSIVEADDRUSE` on Windows),
    `serve` exits if a helper already answers, `helper install`/`start` start nothing when it
    runs, and `restart` waits for the port to free.

## 0.2.1 (2026-10-30)

- Using it with any AI coding agent, not just Claude Code: `coderstanding guide` prints the full
  reference (`docs/AI_GUIDE.md`) from the installed package; `guide --snippet [--app NAME]` and
  `guide --write <file>` add a short block to a repo's agent instructions (`AGENTS.md`, `CLAUDE.md`,
  `.github/copilot-instructions.md`, `.cursor/rules/coderstanding.mdc`). New README section
  "Using it with AI coding agents".

- Fixes #1 (Windows install):
  - Install docs: `py -m pipx ensurepath` and opening a new terminal are required before `pipx`
    works; `py -m pipx install <wheel>` is the fallback without PATH changes.
  - `serve` (and so the helper) starts with no project registered instead of exiting; the page shows
    its empty state and `/api/apps` is empty. `serve --service` startup errors now also reach a
    console's stderr and exit non-zero.
  - `helper install` starts the helper (`--no-start` to skip); `helper install` and `helper start`
    verify it answers on 127.0.0.1:8517 and otherwise print the last log lines and the log path.
    One line of next steps (adding a project) follows.
  - The tray hint says `pipx inject coderstanding PySide6` for a pipx install, else
    `<python> -m pip install "coderstanding[tray]"`.

## 0.2.0 (2026-10-30)

- First installable release (pre-release, first for Windows): graph viewer, Projects, Tours,
  Specs and Notes, comments, the slim CLI for AI agents, and the offline guarantee.
