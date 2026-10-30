# Coderstanding: guide for AI coding agents

Coderstanding prints a repo's structure (classes, connections, functions, notes, specs, tours) as
short text, so you read the map instead of grepping and opening files. It works with any agent
that can run shell commands (Claude Code, GitHub Copilot, Cursor, others). Everything is local and
offline: no code leaves the machine. `coderstanding guide` prints this file from the installed
package; `coderstanding <command> --help` lists every option.

## Quick start
1. Find the app name: the repo's agent instructions name it. Not registered yet?
   `coderstanding add <app> <repo folder>` (add `--data DIR` to keep the map out of the repo).
   `add` does the first scan (`--no-scan` skips it). Commands read the saved map and
   never rescan by themselves, so after code changes, when you need fresh structure, run
   `coderstanding rescan <app>` first (it is cheap: unchanged files come from the cache).
   Registered apps are in `apps.json` in the per-user config folder (`~/.config/coderstanding/`,
   `%APPDATA%\coderstanding\` on Windows).
2. `coderstanding graph <app> 1` is the top level. Then `node`, `zoom`, `outline`, `find`,
   `impact` as below; open real files only for what they point at.
3. Cheat sheet:

| You want | Run |
|---|---|
| the big picture | `graph <app> 1` (`0` = domains, higher = more detail) |
| one class | `node <app> <name>`; who calls what inside: `--functions` |
| one connection | `zoom <app> <from> <to>` |
| a name or topic | `find <app> <words...>` |
| a class's functions with line ranges | `outline <app> <name>` |
| who breaks if I change it, tests to rerun | `impact <app> <name[#function]>`, `tests <app> <name>` |
| context to paste into another agent | `brief <app> <targets...>` |
| what the user sees in the viewer | `view <app> --level N [--focus A A>B A#f]` |
| the plan, the user's notes | `specs <app>`, `memos <app>` |
| how a flow works | `tours <app>`, `tour <app> <id>` |
| problems | `warnings <app>` |
| why classes look unconnected | `diagnose <app> [--examples]` |
| proof that it is offline | `privacy` |

## Commands in detail
- Before reading files in a registered repo, run `coderstanding graph <app> 1` to see the top level.
- To understand one connection, `coderstanding zoom <app> <from> <to> [--level L]`; one class,
  `coderstanding node <app> <name> [level]`. `graph <app> 0` shows domains. Headers print the
  user's level name next to the number (`L2 Systems`); a user-named level count caps the levels.
- Names: full id or a unique short name (`Net`, `net.gd`, `src/net.gd`); ambiguous names
  list the candidates. Options: `--concrete`, `--calls` (all calls), `--hide-type .gd`
  (repeatable; `--no-scenes` = `--hide-type .tscn`), `--no-external`, `--data DIR` (keep graph/notes outside the repo).
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
- `coderstanding diagnose <app>`: when many classes look unconnected, why: imports that found no
  file, counted by pattern (e.g. "34 imports starting with an alias marker (`@/` 34), no tsconfig
  paths match", "12 imports of folders with no index file"), tsconfig/jsconfig and workspace
  package counts, and unresolved names. Counts only: no app name, file names, paths or code, so
  it is safe to paste into a public bug report (e.g. a GitHub issue) for a private code base.
  `--examples` adds up to three short examples per pattern with every name replaced by a letter
  (`@/a/b.js`); read them before pasting.
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
  (Markdown and `[[links]]` work; your comments are marked AI). **After acting on a comment,
  reply or resolve it**: `comment <app> --reply ID "Done: ..."`, `comment <app> --resolve ID`
  (`--reopen ID` undoes). Comments on a deleted step are listed as lost in `warnings`.
- Open real files only for the classes and calls the output points at.

## Token-saving workflow
1. `graph <app> 1`, then `find` for the topic: names and one-line hints, no file reads.
2. `outline <app> Name`, then read only the `file:first-last` range you need.
3. Before editing: `impact` (dependents) and `tests` (what to rerun). After: rerun just those tests.
4. Hand work to another agent with `brief`, not a pasted file dump.
5. Prefer these over grep: the graph knows calls and types, grep matches text.
6. The map is a guide, not proof: dynamic code (`get_node("...")`, untyped vars, reflection)
   can hide links, so check the code before acting on `warnings`.

## Etiquette
Your `set`, `spec`, `memo`, `tour --draft` and `comment` writes are AI drafts: the user accepts
them in the viewer, and any value the user wrote is kept. Do not overwrite or argue with human
values; tell the user instead. Check a tour's open comments before editing it.

## Privacy
Coderstanding makes no outside network calls (a guard refuses every non-loopback connection);
`coderstanding privacy` prints its state. Safe on company code. Do not paste its output anywhere
the code should not go.

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
- What goes in each note field, and what does not: see "Writing note fields" below. Set
  `review` and `rules` text with `\n` for a new line.
- Also `status=stable|wip|fragile|deprecated`, `tags=net,audio`, `rules="no X\nno Y"`
  (`\n` = new line). Test classes are left out of `curate`.
- `set` writes AI drafts (author `claude`, `ai: true`, whichever agent you are); human values (any other author) are
  never overwritten. `--by NAME` writes as that person, only when the user asks. Function
  notes: `set <app> 'Net#join_room' hint="..."`.
- Stale notes: each hint/description/review/rules value stores a fingerprint of the code it
  describes (class lines, function body, or the lines where a connection is made) when written.
  If that code changed since, `node`/`zoom`/`view` print `(stale)` after the value and
  `warnings` lists `stale (code changed since the note): A hint, A#f review`. Do not trust a
  stale note: read the code, then rewrite it, or if it is still right
  `coderstanding set <app> --still-true A 'A#f' A>B`. Notes written before this feature have no
  fingerprint and are never marked stale.

## Writing note fields
The built-in rules (source: `docs/field_rules.json`, also shown by the viewer's "?" tips). A team
may change them per project: `coderstanding fields <app>` shows the effective rules (marking
the project's own), `fields <app> --set hint.max=120 description.code_check=off hint.what="..."
house="short house rules for AI notes"`, `fields <app> --reset hint` (or `all`). They live in
`fields.md` in the project's data folder, shared through git like tours; `coderstanding guide
--app <app>` prints this section with the project's rules. When you write a note and the project has
house rules, follow them over the defaults. `set` only reminds you (never refuses) when an AI-written
hint is too long or a hint/description reads like code.

<!-- field-rules:begin -->
`hint` and `description` are for a new developer who is not very technical and does not know the code base. Say what the class or connection is FOR and where it fits; never how the code works. Do not restate code, signatures, types or algorithms, and explain any jargon in a few plain words. Implementation detail belongs in code comments, or in `review` / `rules` for their own purposes.

- `hint`: One short plain line (about 80 characters) shown on the box or line: what it is for. Max 100 characters.
  - For: A new developer who is not very technical and does not know this code base.
  - Not: How the code works: no types, function names, signatures or algorithms; no unexplained jargon.
  - Good: `Sends chat messages between players; everything that talks to the server goes through it`
  - Bad: `Uses a Dictionary<int, Peer> and calls rpc_id() per message`
- `description`: A few plain sentences: what the class (or connection) is for, what uses it, and what a newcomer should watch out for.
  - For: A new developer who is not very technical and does not know this code base or what the class is for.
  - Not: A walk through the code: no restating of functions, types or algorithms (that belongs in code comments); no problems (use review).
  - Good: `Keeps the list of items in a shop and their prices. The checkout and the shelf display both read from it. Changing a price here changes it everywhere at once.`
  - Bad: `Holds a List<Item> and sorts it with a comparer in Refresh(); GetPrice() returns item.price * taxRate.`
- `review`: Findings from a code review: smells, risks, suggested splits with concrete targets.
  - For: Developers about to change or clean up this code.
  - Not: What the class is for (hint/description) or do-nots (rules); no praise or plain description.
  - Good: `3800 lines; move the chat functions into a separate ChatLog class`
  - Bad: `Handles the shop.`
- `rules`: Things never to do here, one rule per line; shown to anyone (and any AI) changing this code.
  - For: Anyone, human or AI, about to edit this code.
  - Not: Descriptions, wishes or findings; only firm do-nots, each with its reason if it is not obvious.
  - Good: `Never call the Server from here; go through the Chat system`
  - Bad: `This class is complicated`
- `status`: stable / wip / fragile / deprecated: how safe it is to change.
  - For: Anyone deciding how carefully to touch this code.
  - Not: Anything else: reasons go in review or rules.
  - Good: `fragile`
  - Bad: `needs work`
- `tags`: Short free labels for search and grouping, comma separated.
  - For: Anyone searching or filtering the map.
  - Not: Sentences, or the class's own name; no duplicates of status or domain.
  - Good: `chat, audio`
  - Bad: `this is the thing that plays sounds`
- `level`: How high up it shows: 1 is the top picture, higher numbers are more detail.
  - For: Anyone reading the map from the top down.
  - Not: How important the class is; it is how much detail you need before it appears.
  - Good: `2`
  - Bad: `5 because it is boring`
- `domain`: The broad area it belongs to, in one or two plain words.
  - For: A newcomer scanning the map for an area.
  - Not: A code folder or namespace; a sentence; a new name when an existing domain fits.
  - Good: `Chat, Shop, Players`
  - Bad: `Scripts/Net/Impl`
<!-- field-rules:end -->

## Flags and code descriptions
- Entry flags name the mechanism: `godot-main` (root script of `run/main_scene`),
  `godot-main-scene`, `autoload`, `program-main` (C# and Java `Main`/top-level statements, C/C++
  `main`, Kotlin top-level `main` / `@JvmStatic main` / Android `Application`), `script-main` (Python `__main__`, Node shebang or package.json `bin`/`scripts`; a
  tool, level 2 when the app has a start). Every entry also has `entry`.
- A node's `description` falls back to the code's own header comment (`description_by:
  "code"`); your AI drafts and the user's text replace it. Curate only what the code leaves
  unsaid. GDScript scripts without `class_name` are named by file (`main.gd`).
- Calls from a class to itself are internal calls: they show when the class is opened.

## Claude Code only: the hook
Other agents skip this. The hook (`coderstanding hook-run`, stdlib-light, never blocks or errors) adds context on SessionStart
(cwd in a registered app) and on PreToolUse for Grep/Glob/Read and Bash `grep|rg|find|cat`
inside a registered repo or its worktrees: once per session per app, it suggests
`coderstanding find|outline|impact|brief|graph|node`. `coderstanding hook` prints the JSON to merge
into `~/.claude/settings.json` (it calls the installed `coderstanding`, no repo path).
State: `$XDG_RUNTIME_DIR/coderstanding-hook-<session_id>.json` (temp folder when unset).

## Line for agent briefs and instructions
`coderstanding guide --snippet --app <app>` prints a ready block for a repo's agent instructions
(`AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, a Cursor rule); `guide --write FILE`
adds it. In a one-off brief to another agent: "This repo is registered in Coderstanding: start with
`coderstanding graph <app> 1`, then zoom/node/specs instead of broad grepping." Paste any
`rules X:` lines for classes that agent touches.

Viewer for the user (you do not need it): `coderstanding serve [app]` serves every registered app on one port; the user switches apps, presses Refresh and adds repos from the toolbar.
