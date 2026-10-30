# Coderstanding for Claude and agents

`coderstanding` is on PATH (see README). The `coderstanding` Claude skill
(`~/.claude/skills/coderstanding/SKILL.md`, installed by `coderstanding skill install`) points here.
Keep it short; it is meant to be pasted into briefs or loaded by a skill.
Registered apps: `~/.config/coderstanding/apps.json`.

## When to use it
- Before reading files in a registered repo (`coderstanding add <app> <path>` registers one),
  run `coderstanding graph <app> 1` to see the top level.
- To understand one connection, `coderstanding zoom <app> <from> <to> [--level L]`; one class,
  `coderstanding node <app> <name> [level]`. `graph <app> 0` shows domains.
- Names: full id or a unique short name (`Net`, `net.gd`, `src/net.gd`); ambiguous names
  list the candidates. Options: `--concrete`, `--calls` (all calls), `--no-scenes`,
  `--no-external`, `--data DIR` (keep graph/notes outside the repo).
- Level 1 also holds hubs (classes with too many connections, `[hub 80]` = 80 neighbours); the
  `graph` header lists them. `coderstanding settings <app> [hub_threshold=N|auto|off]` shows or
  changes the threshold (rescans next command); also `add ... --hub-threshold`, `scan ... --hub-threshold`.
- Specs (the plan as nested epics, `specs/<id>.md` in the app's data folder) and memos (the
  viewer's "Notes": comments and information, `memos/<id>.md`; not the per-class notes of `set`)
  share one set of commands: `coderstanding specs|memos <app>` prints the tree (`id: Title [L2,
  implemented, p:high, i:low, 3 links, ai] +N finer`; `--level N` folds finer ones, `--zoom id`
  one item and its children); `specs <app> <words>` searches titles, text and links at any level
  and prints each hit's path (`A › B › C`); `--stage todo`, `--priority high`, `--importance high`
  filter and `--sort priority|importance` sorts (high first). `spec|memo <app> <id>` prints one
  (path, children, links, problems such as unknown links, then the text). `node` and `zoom` end
  with the `specs:` / `memos:` that link that class or line. Read the spec before working on a
  feature it covers.
- Spec stages: idea, todo, in-progress, review, implemented, dropped. Memo stages: todo,
  in-progress, done, na (N/A, the default: just information). Priority (work: how soon) and
  importance (how much it matters): high, medium, low.
- Writing specs and memos: `spec <app> --add "Title" [--parent id] [--links A,B>C,D#f] [--stage s]
  [--priority p] [--importance p] [--level N] [--text "..."]` (id from the title; `memo` the same);
  change one with `spec <app> <id> --set stage=implemented links+=C links-=A priority=high
  text="..."` (or the flags); `--delete` (its children move up to its parent). Your writes are AI
  drafts the user accepts in the viewer; a field the user filled in is kept (`kept your stage`):
  tell the user instead. `--by NAME` writes as that person (only when asked). When you finish a
  feature, move its spec to `review` (or `implemented` when the user said so).
- To see packages/addons and who uses them, `coderstanding deps <app>`.
- After changing a class, `coderstanding tests <app> <name>` lists the test files to run
  (`--deep` adds the tests of its direct users). Tests are hidden in graph/zoom/node unless
  `--tests`.
- `coderstanding warnings <app>`: unused classes, god classes, dependency cycles, unsure lines
  and unresolved outside refs, one line per kind. Godot apps also get `signal-unconnected`,
  `signal-unemitted`, `bad-handler` (`_on_..` handlers only) and `autoload-unused`, with the
  signal / handler names in `[..]`; they are hints (dynamic connects, string-named signals and
  runtime-added functions are not seen), so check the code before acting. Per-folder ignore rules: `coderstanding rules <app>`
  (`add "tools/**" ignore=unresolved [lang=..] [kind=..]`, `remove N`); `warnings --all` shows why each is ignored.
  Architecture rules (`layer` warnings, one per bad line, with file:line and the rule's why):
  `rules <app> add-layer FROM TO --forbid|--only [--kind K] [--why TEXT]` (FROM/TO = folder glob,
  `domain:<name>` or `tag:<tag>`; `--only`: only FROM may use TO); `rules` numbers both kinds,
  `remove N` removes either. Turn rules written in a project's docs into these, then show the user `rules`.
- Who calls what inside a class: `coderstanding node <app> <name> --functions` prints
  `net.gd#join_room > media*: upload ×2, clean_file_name` per function, an `in:` line of its
  callers (`A#f in: ...` when it calls nothing), `A (fields) > B` for lines with no function
  (field types, bases), and `no calls:` for the rest.
- The user's "Copy for AI" pastes `coderstanding view <app> --level N [--zoom A [B]]
  [--expand X ...] [--select X|A>B|A#f]` as its first line, then the same text the command
  prints: rerun or adjust that command instead of asking what is on screen.
- Maps stay on this PC: the hosted page is only the viewer and talks to the local helper
  (`coderstanding serve`, the `coderstanding-helper` user unit). There is no `sync` and nothing
  to push to the server; deploying the hosted page only copies the viewer, when asked.
- Registry upkeep: `coderstanding rename <old> <new>`; `coderstanding remove <app>` (files stay),
  `--delete-data` (graph, cache, layout), `--delete-notes` (also notes, specs, memos, tours, settings:
  only when the user asks). The viewer's "Projects…" modal does the same.
- Before opening a file: `coderstanding outline <app> Net` lists its functions with
  `file:first-last` lines, so you read only that range (`Net#join` = one function).
- Looking for something by name or topic: `coderstanding find <app> word [word...]` (all words
  must match name, notes or code doc; 20 hits, `--all` for more).
- Before changing a class or function: `coderstanding impact <app> Net[#join] [--depth N]` shows
  who depends on it by distance and the tests to rerun.
- Briefing another agent: `coderstanding brief <app> A B#f` prints a short block (ranges, notes,
  neighbours, tests, warnings) to paste into the brief.
- Tours (guided walks through the code, `.coderstanding/tours/<id>.md`): `coderstanding tours
  <app> [words]` lists them (`id Title N steps [author] 2 ai, 1 changed`); `coderstanding tour
  <app> <id>` prints per step `N. Title {targets}`, the text and only that step's code lines with
  today's numbers (`>` = highlighted, `(changed)` = the code changed since the step was written,
  `(lost: X)` = a target is gone). `--step N` prints one step with the `view --focus ...`
  command for its graph first; `--marked` only the highlighted lines as `path:line`; `--no-code`
  titles, targets and text; `--audio` which steps have a recording; `--md [-o file]` a Markdown
  document to share. Read a tour before exploring a flow it covers.
- Writing tours: when the user asks how a flow works, draft one: `coderstanding tour <app> <id>
  --draft "How X works" A#f A>B C ...` (one step per target in that order, anchored on the
  function or the call, text from notes and docs; saved as an AI draft the user accepts in the
  viewer). Then improve it: `--step N --text "..."` / `--title "..."`, or edit the file (front
  matter: `key`, `targets`, `code: {at, lines: +a..+b, mark: [+n]}`; one `## Title` section per
  step). After checking a `(changed)` step is still right: `tour <app> <id> --still-true [--step N]`.
  `view <app> --focus A A>B A#f` highlights targets like a step does.
- Tour comments (`.coderstanding/tours/<id>.comments.jsonl`, committed): the user, or a
  co-worker through git, leaves comments on a tour, a step or a code line of a step. **Before
  editing a tour, check its open comments**: `coderstanding tour <app> <id> --comments [--all]`
  (`[id] step 2 Title path:line author: text`, replies indented; `--all` adds resolved threads);
  the lean `tour` text shows `(2 open comments)` on a step's title line. Comment with
  `coderstanding comment <app> tour:<id>[#<step N>] "text" [--line N] [--quote "words"]`
  (Markdown and `[[links]]` work; you write as `claude`, marked AI). **After acting on a comment,
  reply or resolve it**: `comment <app> --reply ID "Done: ..."`, `comment <app> --resolve ID`
  (`--reopen ID` undoes). Comments on a deleted step are listed as lost in `warnings`.
- Open real files only for the classes and calls the output points at.

## Reading the slim text
- `A > B` direct, `A ~> B (via n)` runs through n hidden classes.
- `*` singleton, `[I]` interface, `[addon]`/`[nuget]` outside code, `[scene]` scene.
- After `:` the hint; `[kinds]` if none; `{calls}` = members called.
- `out:` line in zoom output = faded connections leaving the zoom.
- `node` line: `[kind] file:line Llevel 120 lines, 8 members [status] #tag: hint`
  (`(file)` = the file's lines, no own span); then `warn:`, `tests:`, `rules:` lines.
- `rules X:` lines (node, and both ends in zoom) are the user's "never do X here" rules:
  follow them and paste them into agent briefs that touch X.

## Curating (levels, domains, hints)
- `coderstanding curate <app>` lists classes that still need a level/domain/hint.
- Answer with one batch: `coderstanding set <app> --file batch.txt`,
  lines like `Renderer level=3 domain=Rendering hint="draws frames"`; `A>B hint="..."` for a
  connection; `key=` clears. One-off: `coderstanding set <app> Renderer level=3`.
- What each note field means (source of truth: `viewer/field_help.js`, which also feeds the
  "?" tips in the viewer; keep this wording in sync, a test checks it). Put things in the
  right one:
  - `hint`: One line on the box or line: what it is or does.
  - `description`: What the class is and what its functions do; for a connection, what it represents and why it exists. No problems here.
  - `review`: Findings from a code review: smells, risks, suggested splits with concrete targets. Set with `review="..."`, `\n` = new line.
  - `rules`: Things never to do here; shown to anyone (and any AI) changing this code. One rule per line.
  - `status`: stable / wip / fragile / deprecated: how safe it is to change.
  - `tags`: Free labels for search and grouping, comma separated.
  - `level`: How high up it shows: 1 is the top picture, higher numbers are more detail.
  - `domain`: The broad area it belongs to.
- Also `status=stable|wip|fragile|deprecated`, `tags=net,audio`, `rules="no X\nno Y"`
  (`\n` = new line). Test classes are left out of `curate`.
- `set` writes AI drafts (`by: claude`, `ai: true`); human values (any other author) are
  never overwritten. `--by NAME` writes as that person, only when the user asks. Function
  notes: `set <app> 'Net#join_room' hint="..."`.
- Stale notes: each hint/description/review/rules value stores a fingerprint of the code it
  describes (class lines, function body, or the lines where a connection is made) when written.
  If that code changed since, `node`/`zoom`/`view` print `(stale)` after the value and
  `warnings` lists `stale (code changed since the note): A hint, A#f review`. Do not trust a
  stale note: read the code, then rewrite it, or if it is still right
  `coderstanding set <app> --still-true A 'A#f' A>B`. Notes written before this feature have no
  fingerprint and are never marked stale.
- The graph is a map, not proof: dynamic code (`get_node("...")`, untyped vars) can hide links.

## Flags and code descriptions
- Entry flags name the mechanism: `godot-main` (root script of `run/main_scene`),
  `godot-main-scene`, `autoload`, `program-main` (C# `Main`/top-level statements, C/C++
  `main`), `script-main` (Python `__main__`, Node shebang or package.json `bin`/`scripts`; a
  tool, level 2 when the app has a start). Every entry also has `entry`.
- A node's `description` falls back to the code's own header comment (`description_by:
  "code"`); your AI drafts and the user's text replace it. Curate only what the code leaves
  unsaid. GDScript scripts without `class_name` are named by file (`main.gd`).
- Calls from a class to itself are internal calls: they show when the class is opened.

## Hook: nudge Claude Code toward the graph
The hook (`coderstanding hook-run`, stdlib-light, never blocks or errors) adds context on SessionStart
(cwd in a registered app) and on PreToolUse for Grep/Glob/Read and Bash `grep|rg|find|cat`
inside a registered repo or its worktrees: once per session per app, it suggests
`coderstanding find|outline|impact|brief|graph|node`. `coderstanding hook` prints the JSON to merge
into `~/.claude/settings.json` (it calls the installed `coderstanding`, no repo path).
State: `$XDG_RUNTIME_DIR/coderstanding-hook-<session_id>.json` (temp folder when unset).

## Line for agent briefs
"This repo is registered in Coderstanding: start with `coderstanding graph <app> 1`, then
zoom/node/specs instead of broad grepping."

## Planned: diagrams and links
- `[[...]]` in hints/descriptions/specs links a class, `A→B` connection, `spec:id` or
  `diagram:id`. Follow a diagram link with `coderstanding diagram <app> <id>` (Mermaid text).

Viewer for the user: `coderstanding serve [app]` serves every registered app on one port; the user switches apps, rescans and adds repos from the toolbar.
