---
name: coderstanding
description: Read a code base's structure cheaply before opening files. Use when starting work in, exploring, planning or briefing agents on a repo registered in Coderstanding (~/.config/coderstanding/apps.json), or when the user asks to register/scan a repo or mentions Coderstanding.
---

# Coderstanding

`coderstanding` (on PATH) prints a repo's class graph as slim text, so you read structure
instead of grepping files.

1. Check the app name: `cat ~/.config/coderstanding/apps.json`. Not registered yet?
   `coderstanding add <name> "<repo path>" --data ~/.local/share/coderstanding/<name>`.
2. Start with `coderstanding graph <app> 1` (own code only; `--all` adds outside packages; `graph <app> 0` starts with the overview: a flow sentence and one line per domain, written with `set <app> "domain:*" description="..."`), then `node`, `zoom`, `tests`, `warnings`. Before committing, `coderstanding tests <app> --diff` lists the test files for your whole diff (and dependents): run those, not just your new tests.
3. Know a name or topic, not where it lives: `coderstanding find <app> <words...>` (fuzzy; searches names and notes, spec/memo/tour text; `find <app> --text <regex> [--path <prefix>]` = literal text in any file (code first, 40 lines, then a hit count per remaining file), with the enclosing function or heading; `--tests` adds callers and nearest tests).
   About to read a class: `coderstanding outline <app> <Name>` first, then
   `coderstanding read <app> Name#func [...]` (numbered source + hint; `--signatures`, `--context N`). Every reading command first rescans a map older than the files (one stderr line, "map is older than the files (N changed): rescan" if it cannot: run `rescan`). Targets: `Class#f`, `Class`, a bare function name (several: lists them), `path:A-B`, `path:line` (function around it; the path may be a module stem like `resolver:230-300`; several files: lists them), `module#Class`, `path` (code file: header + defs; .md: headings), `doc.md#Heading`; a miss says "did you mean". Every command that takes a code target (`brief`, `impact`, `outline`, `zoom`, `node`, `view`, `set`, `setup`...) takes `Class`, `Class#f`, or a bare function name the same way (one match is used; several list the targets), and a miss says "did you mean"; the path and `.md` forms are `read` only.
   The user pastes location refs, lines starting with `@<app> ` (e.g. `@myapp src/a.py:10-20 (Net#join +2..+12)`, `@myapp tour intro step 3`, `@myapp comment 4f2a`): run `coderstanding ref '<the pasted lines>'` to read what they point at (code, tour step, comment thread, spec/memo; a `warning:` line says the lines moved).
   In a git worktree of the repo (another branch), run commands from inside it or add
   `--repo <worktree>`: lines then match that branch, notes still come from the app. About to change a class or function (`Name#func`):
   `coderstanding impact <app> <target>` for dependents, the nearest tests to rerun (`--all`: all) and the tests and
   docs naming it (a constant or env var name works too). Every line a name is on, in code,
   tests, docs, deploy and scripts: `coderstanding uses <app> <Name>`. Env vars read/set/
   documented with gaps: `coderstanding env <app> [--prefix WOOF_]`. Repeated string literals:
   `coderstanding dupes <app>`. Briefing another agent: paste `coderstanding brief <app> <targets...>`. `graph` ends with the globals
   (autoloads, class_name) and how tests reach them (e.g. `root.get_node("Media")`): use those
   names. Writing a test: `coderstanding setup <app> <a similar test file>` lists the helpers,
   hooks, base class and globals to copy, and what sibling tests have that it lacks.
4. Learning how a flow works (or explaining it to the user): `coderstanding tours <app>`
   lists guided tours; `coderstanding tour <app> <id>` prints each step's text and only its
   code lines (`--step N` one step, `--marked` just the highlighted lines). Asked to explain a
   flow, write one: `tour <app> <id> --draft "Title" A#f A>B ...` (an AI draft the user accepts),
   then fix steps with `--step N --text ...` and point at lines with `--step N --note 50-51 "..."
   [--cue "words"]` (popup notes; `--drop-note 50-51`); `(changed)` steps need re-checking.
   What the graph shows while it plays: `tour <app> <id> --view lines=all selfcalls=0` (whole
   tour) or `--step N --view boxtext=full` (one step; `key=` removes one; names as the viewer's URL options).
   `tour <app> --draft-start ["Title"]` drafts the newcomer's "Start here" tours from the entry
   points' main paths (`start: true` tours); then improve their texts.
   Before editing a tour, read its open comments (`tour <app> <id> --comments`; steps show
   `(2 open comments)`): they are what the user or a co-worker wants changed. After acting on
   one, reply (`comment <app> --reply ID "Done: ..."`) or resolve it (`comment <app> --resolve ID`).
5. Planning or building a feature: `coderstanding specs <app> [words]` finds its spec (tree, or
   hits with their paths; `--stage todo --sort priority` = the work queue), `spec <app> <id>`
   reads it; `memos` / `memo` are the user's Notes (same options). Keep specs current: `spec <app>
   --add "Title" --parent id --links A,A>B --stage todo --priority high`, `spec <app> <id> --set
   stage=review links+=C` (AI drafts the user accepts; fields the user set are kept).
6. Open real files only for the classes and calls the output points at.
7. Put this line in agent briefs for that repo: "This repo is registered in Coderstanding:
   start with `coderstanding graph <app> 1`, then zoom/node/specs instead of broad grepping."
   Paste any `rules X:` lines it prints for classes the agent touches, and add: "If Coderstanding
   fails you or lacks something you need, say so in your report" (step 8).
8. Struggles and missing features: whenever a command errors, gives wrong or useless output, makes
   you fall back to grep/reading files, or you need something it doesn't have, (a) tell the user in
   your reply, one line: "Coderstanding issue: <what>", and (b) log it as an issue file (below).
   One file per issue; if a file for the same issue exists, add a dated line to it instead.
   Subagents report it to the lead; the lead logs it.

Issue log: keep a folder of issue files (app, command, expected, got, workaround) wherever the user wants them.

Writing `hint`/`description` with `set`: they are for a newcomer who does not know the code, so say what
the class is FOR, not how it works (`coderstanding fields <app>`, "Writing note fields" in the guide).

Full reference (how to read the text, curating with `set`): run `coderstanding guide` (the same
text is `AI_GUIDE.md` in this skill's folder).
