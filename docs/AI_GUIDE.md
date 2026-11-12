# Coderstanding: guide for AI coding agents

Coderstanding prints a repo's structure (classes, connections, functions, notes, specs, tours) as
short text, so you read the map instead of grepping and opening files. It works with any agent
that can run shell commands (Claude Code, GitHub Copilot, Cursor, others). Everything is local and
offline: no code leaves the machine. `coderstanding guide` prints this file from the installed
package; `coderstanding <command> --help` lists every option.

## Quick start
1. Find the app name: the repo's agent instructions name it. Not registered yet?
   `coderstanding add <app> <repo folder>` (add `--data DIR` to keep the map out of the repo).
   `add` does the first scan (`--no-scan` skips it). Commands check the map
   against the files first and rescan by themselves when files changed (cheap: unchanged files come
   from the cache; one stderr line says so). If it cannot (read-only data folder) it prints
   "map is older than the files (N changed): rescan": then run `coderstanding rescan <app>`.
   Registered apps are in `apps.json` in the per-user config folder (`~/.config/coderstanding/`,
   `%APPDATA%\coderstanding\` on Windows).
2. `coderstanding graph <app> 1` is the top level. Then `node`, `zoom`, `outline`, `find`,
   `impact` as below; open real files only for what they point at.
3. Cheat sheet:

| You want | Run |
|---|---|
| the big picture | `graph <app> 1` (`0` = domains, higher = more detail; lines to outside packages are one count, `--all` lists them) |
| one class | `node <app> <name>`; who calls what inside: `--functions` |
| one connection | `zoom <app> <from> <to>` |
| a name or topic | `find <app> <words...>` (literal text: `find <app> --text <regex>`) |
| a class's functions with line ranges | `outline <app> <name>` |
| globals (autoloads, class_name) and how code and tests reach them | the end of `graph <app> 1` (`outline <global>` for one) |
| what to copy when writing a test | `setup <app> <test file>`: its hooks, helpers, bases, globals and what sibling tests have that it lacks |
| a function's code with line numbers | `read <app> <Class#f \| Class --signatures \| bare name \| path:A-B \| path \| doc.md#Heading>...` |
| lines that match my branch's worktree | run from the worktree, or add `--repo <worktree>` |
| who breaks if I change it, tests to rerun | `impact <app> <name[#function]>` (also a constant or env var: lists the tests and docs naming it), `tests <app> <name>` |
| every line a name is on (code, tests, docs, deploy, scripts) | `uses <app> <Name>` |
| env vars: where read, set, documented, gaps | `env <app> [--prefix WOOF_]` |
| the same string literal in several files | `dupes <app> [--tests]` |
| where bugs and rework are likely (size x git churn x untested) | `hotspots <app> [--top N] [--days D]` |
| how much is done (described, tested, built, the project's own tracks) | `progress <app> [--track NAME] [--spec ID]` |
| context to paste into another agent | `brief <app> <targets...>` |
| what the user sees in the viewer | `view <app> --level N [--focus A A>B A#f] [--domain D]` |
| the plan, the user's notes | `specs <app>`, `memos <app>` |
| how a flow works | `tours <app>`, `tour <app> <id>` |
| problems | `warnings <app>` |
| why classes look unconnected | `diagnose <app> [--examples]` |
| proof that it is offline | `privacy` |

## Commands in detail
- Before reading files in a registered repo, run `coderstanding graph <app> 1` to see the top level.
- To understand one connection, `coderstanding zoom <app> <from> <to> [--level L]`; one class,
  `coderstanding node <app> <name> [level]`. `graph <app> 0` shows domains: first the overview (a flow sentence, then one line per domain: hint or
  description, key classes, who it talks to), then the lines between them. Headers print the
  user's level name next to the number (`L2 Systems`); a user-named level count caps the levels.
- Names: full id or a unique short name (`Net`, `net.gd`, `src/net.gd`), or a nickname (below); ambiguous names
  list the candidates. Options: `--concrete`, `--calls` (all calls), `--hide-type .gd`, `--hide-file-kind Data`, `--hide-kind signal`,
  `--tag chat` (only classes with that `tags` note; several = any of them), `--hide-tag chat`
  (repeatable; `--no-scenes` = `--hide-type .tscn`), `--no-external`, `--no-comm` (hide socket / network endpoint nodes, tagged `[comm]`), `--data DIR` (keep graph/notes outside the repo).
- Network messages (remote calls, Godot `@rpc`): each handling class has one message box per sending
  class, `<Class> messages ← <Sender> [comm]` (id `comm:<class>#<sender>`; messages nobody sends:
  `<Class> messages (not sent)`; a message sent from two classes is a row in both). Lines read sender `>` message node `>` handler, the
  calls in `{}` being the message names. `node <Class>` prints `sends: → _c_move (client→server)` and
  `handles: ← _s_chat (server→client)`; `node "<Class> messages"` lists every message with its
  direction. Direction comes from the mode: `any_peer` = client→server (`any` when some sender
  broadcasts it with `.rpc()`), else server→client; names are never read. Senders are every mention of
  the name (`f.rpc_id(1, ..)`, `rpc("f")`, `"f"` passed to a send helper, `f` passed as a Callable), not
  plain calls `f()`. `find` marks the handler function `message client→server`.
- Level 1 also holds hubs (classes with too many connections, `[hub 80]` = 80 neighbours); the
  `graph` header lists them. `coderstanding settings <app> [hub_threshold=N|auto|off]` shows or
  changes the threshold (rescans next command); also `add ... --hub-threshold`, `scan ... --hub-threshold`.
  `rescan <app> --setting hub_threshold=20` tries a setting for one run without saving it.
- Wrong level guess everywhere? You may suggest `coderstanding rescan <app> --relevel ai` (clears only
  AI-drafted level notes, backs them up, keeps level names). Never run `--relevel all` (also clears the
  human's level notes) or `--reset-level-names` without asking the user first.
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
  (`--deep` adds the tests of its direct users); `tests <app> --diff [REF]` does it for the git diff (REF default: merge base with main; uncommitted included) and lists changed units with no test (per changed function its direct tests, else its class's; dependents' tests only if a def line changed; `many tests reach` = run `impact` for those; it prints every test file, never "+N more"; also tests that quote a CLI command whose runner (`run_<command>`) sits above the changed function in its callers, an `e2e:` line with `node tests/e2e/run.mjs --only ...` when viewer code changed, and `tour steps over this code (may go stale)`, which `impact` prints too). Tests are hidden in graph/zoom/node unless
  `--tests`.
- `coderstanding warnings <app>`: unused classes, god classes, dependency cycles, unsure lines
  and unresolved outside refs (not `bpy` or asset files: those are "outside", one `outside:` summary line with the count and top names; `--all` lists every name), one line per kind. `warnings <app> --names [Class]` lists the unresolved names with counts, per class. A `parse-error` line lists files the grammar could not fully parse (`file:line`; declarations there are read as far as possible, the rest may be missing). Godot apps also get `signal-unconnected`,
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
  `file:first-last` lines, so you read only that range (`Net#join` = one function). Its
  `Net#join` names (and `find` hits) go into `read` as they are.
- Pasted refs: the user may paste lines like `@myapp src/a.py:10-20 (Net#join +2..+12)`, `@myapp tour intro step 3` or
  `@myapp comment 4f2a` (copied in the viewer). Run `coderstanding ref '<the pasted lines>'` (or
  `coderstanding read <app> '@myapp ...'`): it prints the code, tour step, comment thread or spec/memo
  they point at, with a `warning:` line when the lines moved. A ref that no longer resolves lists the nearest targets.
- Reading the code itself: `coderstanding read <app> Net#join [Lobby#start ...]` prints each
  function's lines numbered under a `Net#join  file:first-last  — hint` header; `Net --signatures`
  = just each function's first line; `--context N` adds N lines around. Targets: `Class#f`, `Class`, a bare function name (several: lists them), `path:A-B`, `path:line` (function around it; the path may be a module stem like `resolver:230-300`; several files: lists them), `module#Class`, `path` (code file: header + defs; .md: headings), `doc.md#Heading`; a miss says "did you mean". Every command that takes a code target (`brief`, `impact`, `outline`, `zoom`, `node`, `view`, `set`, `setup`...) takes `Class`, `Class#f`, or a bare function name the same way (one match is used; several list the targets), and a miss says "did you mean"; the path and `.md` forms are `read` only. A
  bare name with several matches prints only the ready-to-paste `Class#f` choices; `path:line` outside
  functions = the lines near it; `path` = imports and constants before the first definition, then
  `defs:` as targets.
- Working in a git worktree of a registered repo (another branch): run the reading commands from
  inside it, or pass `--repo <worktree path>`. The worktree gets its own map (in the app's data
  folder, rescanned from the cache when its files change, dropped when the worktree is gone), so
  line numbers match your branch; notes, specs and tours still come from the app. The
  `note: worktree ...` and lost-notes lines print only when they change, not on every call.
- Looking for something by name or topic: `coderstanding find <app> word [word...]` (all words
  must match, fuzzily, in class/function/field names, notes, code docs or linked spec/memo/tour
  text; `crash hour` finds `MAX_PER_HOUR`. That is names and notes; for literal text
  in any file (code, docs, scripts, config) use `find <app> --text <regex>`: one line per hit with
  its function or heading, code first, 40 full lines then a `file: N hits` line per remaining file
  (`--path <prefix>` narrows to a folder, `--all` every line; vendor and minified files are not
  indexed). `find` takes words or `--text`, not both. Name hits: 20, `--all` for more; `--tests` adds each hit's callers and
  nearest tests; `--include-tests` also searches test code).
- Before changing a class or function: `coderstanding impact <app> Net[#join] [--depth N]` shows
  who depends on it by distance, the nearest tests to rerun (`--all`: every one) and the test and doc lines naming it. A name
  that is no class (constant, env var, settings key) starts from the code lines using it.
- Every place a name is used: `coderstanding uses <app> Name` (src / tests / docs /
  deploy+scripts+config, `path:line  line text`; code lines first, comment/help lines after under
  `comments / help:`; src 40 lines then a `file: N hits` line per file; docs, deploy, scripts and
  config are one count line per kind (`docs 3: a.md, b.md`), `--all` for their lines; `audio/volume`
  style keys work). For understanding, not a grep replacement.
- Env vars: `coderstanding env <app> [--prefix WOOF_]`, one line each with where it is read, set
  (compose, Dockerfile, .env, scripts, CI) and documented; gaps flagged `not set`, `not read`,
  `undocumented`.
- Repeated string literals (6+ chars, not one plain word) in 2+ files: `coderstanding dupes <app>`
  (`--tests` adds test code, `--all` every one).
- Hotspots: `coderstanding hotspots <app> [--top 15] [--days 90]` ranks classes and modules (a module counts only its lines outside its classes) by lines x commits
  touching their file x untested (tested ones count a quarter); needs git history.
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
- Start-here tours: a tour with `start: true` in its front matter is the newcomer's path, shown by
  the viewer's Start here button (numbered boxes). `coderstanding tour <app> --draft-start ["Title"]`
  drafts one per main flow from the map (from an entry class, follow the best line: hinted first,
  then most calls, skipping hubs; 2-6 steps; levels as shown at level 1) as AI drafts (`start-<entry>`;
  existing ones are left alone); then improve their step texts. `tours` lists them with `start tour`.
- Writing tours: when the user asks how a flow works, draft one: `coderstanding tour <app> <id>
  --draft "How X works" A#f A>B C ...` (one step per target in that order, anchored on the
  function or the call, text from notes and docs; saved as an AI draft the user accepts in the
  viewer). Then improve it: `--step N --text "..."` / `--title "..."`, or edit the file (front
  matter: `key`, `targets`, `code: {at, lines: +a..+b, mark: [+n]}`, `notes: [{lines: +a..+b,
  text, cue}]`; one `## Title` section per step). Line notes are short callouts the viewer pops
  up on code lines as the step plays (at `cue` = words of the step text, else in order):
  `--step N --note 50-51 "Why this line matters" [--cue "words in the text"]` (today's line
  numbers, inside the step's code; same lines again replaces it), `--drop-note 50-51`; the tour
  text prints them as `note 50-51: text`. After checking a `(changed)` step is still right: `tour <app> <id> --still-true [--step N]`.
  View options while a tour plays (what the step graph shows; the viewer's URL names):
  `tour <app> <id> --view lines=all selfcalls=0 hide_kind=signal,dependency` sets them for the whole
  tour, `--step N --view boxtext=full` for one step (over the tour's), `key=` removes one. Names a
  tour may set: `view=all|concrete external comm tests cards selfcalls` (1/0),
  `filter=status:<s>|tag:<t> boxtext=name|short|full lines=explained|all hide_kind=<line kinds> hide_type=<.ext> hide_file_kind=<Data,Docs,...>`. The
  tour text prints them as `view (whole tour): ...` / `view (this step): ...`. Leave them out
  unless a step reads better that way (e.g. self calls off, every line drawn).
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
- Planned work (not built yet): `coderstanding plan <app>` lists planned and added-manually classes,
  planned functions, hand-made lines and **stubs** (functions with no real body: empty, `pass`,
  `...`, only a TODO/FIXME comment, `raise NotImplementedError`, `throw new
  NotImplementedException/UnsupportedOperationException/NotImplementedError`, Kotlin `TODO()`,
  GDScript `push_error("not implemented")`; tests are left out). Add one:
  `plan <app> --add "Name" [--on Class] [--kind planned|manual] [--note "what it is for"]
  [--stage idea|todo|in-progress|review|implemented|dropped] [--priority high|medium|low]
  [--importance high|medium|low] [--spec <id>] [--where <file or service>] [--tags a,b] [--level N]
  [--links "A>Name,Name>B"]`. `--on Class` makes a planned function on a real or planned class.
  A planned item shows its own stage, else its spec's. If the name matches real code (exact or
  close), the command stops and says so: put notes on the real one (`set`), or repeat with
  `--anyway` only if it really is something new. Later: `set <app> Name stage=todo`,
  `plan <app> --drop Name|Class#fn`, `plan <app> --link Planned Real` (also `Class#planned
  Class#real`) when the code catches up under another name, `plan <app> --keep-both Name`. A
  planned function gets linked by itself when its class gets a real function of the same name
  (its notes carry over). Your adds are AI drafts (`--by NAME` for the user's; `--accept`).
  **Never look for the file of a `[planned]` item: it does not exist yet.**
- Progress: `coderstanding progress <app>` prints one line per track (`Proven 26.9% (67/249) · +3
  since last scan`); built in: Described (classes with a hint), Tested, Built (functions not planned
  and not a stub). `--track NAME` adds per domain, the trend and what is not done (ready-to-paste
  targets); `--spec ID` counts a spec's linked classes and functions. A project's own tracks live in
  its settings.json `tracks` (`settings <app> 'tracks+={json}'` adds, `tracks-=NAME` removes; `{"name", "of": {units, files, domain, tag, file_kind, lang, tests},
  "done": {tag | field [+ "by": "person"] | fact: tested|built|no_warnings}}`); project truth is
  fed by tagging through `set --file` (`Class#fn tags=proven` per line), never by new formats.
- Open real files only for the classes and calls the output points at.

## Token-saving workflow
1. `graph <app> 1`, then `find` for the topic: names and one-line hints, no file reads.
2. `outline <app> Name`, then `read <app> Name#function` (or read only the `file:first-last` range).
3. Before editing: `impact` (dependents) and `tests` (what to rerun). Before committing: `tests <app> --diff` lists the test files for everything the git diff changed (and dependents) and a `pytest` line; run those, not only your new tests.
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
- `[planned]` = not in the code yet (a plan; no file), `A ..> B` = a planned line; `[manual]` =
  real but the scanner can't see it (`where:` on the `node` line). In function lines `f [stub]`
  = no real body yet, `f [planned todo]` = a planned function. The `graph` header ends with
  `plan: 2 planned, 1 manual, 3 stubs` when there is any; `--no-planned` hides planned items.
- After `:` the hint; `[kinds]` if none; `{calls}` = members called.
- File kind (second label next to the domain; Code, Scene, Configuration, Data, Build, Web page, Docs): code is the default and unmarked; other kinds show as `name [data]` in graph/zoom and `file-kind=` on the `node` line. Configuration, Data, Build, Web page and Docs start one level lower than code; scenes do not (an app start keeps level 1). `--hide-file-kind Data` hides one.
- `update_world (F_243e0)` = a nickname, then the real name (`World (C_12)#update_world (F_243e0)`
  for a function). Either name works as a target.
- `out:` line in zoom output = faded connections leaving the zoom.
- `node` line: `[kind] file:line Llevel file-kind=Data 120 lines, 8 members [status] #tag: hint`
  (`(file)` = a file-level unit, module or script: its lines outside its classes, no own span); then `warn:`, `tests:`, `rules:` lines.
- `rules X:` lines (node, and both ends in zoom) are the user's "never do X here" rules:
  follow them and paste them into agent briefs that touch X.

## Curating (levels, domains, hints)
- `coderstanding curate <app>` lists classes that still need a level/domain/hint.
  `--redo` also lists AI drafts to rewrite (`-` missing, `~` AI draft; description too, never human text);
  `--path <prefix>` (repeatable or comma list) keeps only classes whose file starts with it.
- Answer with one batch: `coderstanding set <app> --file batch.txt`,
  lines like `Renderer level=3 domain=Rendering hint="draws frames"`; `A>B hint="..."` for a
  connection; `key=` clears. One-off: `coderstanding set <app> Renderer level=3`.
- The overview sentence (shown first by `graph <app> 0` and on the viewer's level 0): one or two plain
  sentences that tell how the domains work together, domain names as written, in flow order, saved
  as the description of the pseudo domain: `coderstanding set <app> "domain:*" description="..."`.
  Without it a plain one is generated from the lines (the viewer offers an Ask AI button).
- What goes in each note field, and what does not: see "Writing note fields" below. Set
  `review` and `rules` text with `\n` for a new line.
- Also `status=stable|wip|fragile|deprecated`, `tags=net,audio`, `rules="no X\nno Y"`
  (`\n` = new line). Test classes are left out of `curate`.
- Badly named code (a decompilation's `F_243e0`, minified `a.b`, old code): give it a readable
  `nickname` (one word): `set <app> F_243e0 nickname=update_world`, `set <app> 'mod#F_243e0'
  nickname=...` for one of several. Text output then prints `update_world (F_243e0)`, the viewer
  shows the nickname on the box or row with the real name small beside it, `find` searches it, and
  commands take it as a target (`outline <app> update_world`, `World#update_world`). It is a note
  like the others: a human's nickname is never overwritten, a renamed function's note is listed as
  lost, and it is not marked stale when the code changes (the name is still right).
- A project that already has a names table (a decomp's symbol file, a source map): write a small
  script in that project that turns its table into one `set --file` batch, a line per name
  (`mod#F_243e0 nickname=update_world`), and run `coderstanding set <app> --file names.txt`.
  Coderstanding reads no project-specific name formats.
- Classes nothing references in code (plugin registries that load files by name, `__main__`,
  files a tool or engine loads) show `warn: unused`. Say who loads them:
  `set <app> Plugin loaded_by=Registry loaded_how="auto-loaded by the registry"` (`loaded_by` is a
  comma list of class ids). For a whole folder add one rule to the app's `settings.json`:
  `"loaded_by": [{"loaded_by": "<registry class id>", "files": "src/plugins/*.py", "how": "..."}]`.
  Only add links you checked in the code.
- App settings (`coderstanding settings <app> [name=value ...]`; one table in `app_settings.py`; a bad
  item aborts the whole command and the file stays unchanged). Details for some follow below:
<!-- settings:begin -->
  - `hub_threshold=VALUE`: classes with this many neighbours are level 1 hubs (N, auto or off); applies at the next rescan
  - `max_level=VALUE`: the deepest level the level guess uses (1-12); applies at the next rescan
  - `exclude=a,b`: file globs left out of the scan; applies at the next rescan
  - `include=a,b`: folders or globs of git-ignored files scanned anyway; applies at the next rescan
  - `function_macros=JSON_OBJECT`: C macros that define functions, {"REWRITE": "F_{1}"}; applies at the next rescan
  - `domains=JSON_LIST, domains+=ITEM, domains-=KEY`: default domain by folder, item glob=Domain; applies at once
  - `loaded_by=JSON_LIST, loaded_by+=ITEM, loaded_by-=KEY`: who loads the classes of a folder, item glob=ClassId; applies at once
  - `library=JSON_LIST, library+=ITEM, library-=KEY`: rules marking code as an outside library (glob, name:P, tag:T, domain:D); applies at once
  - `tracks=JSON_LIST, tracks+=JSON_OBJECT, tracks-=NAME`: own progress tracks, item {name, of, done} (see `progress`); applies at once
  - `levels=Name0,Name1,...`: names of the depth levels, level 0 first (never rescans); applies at once
<!-- settings:end -->
- A folder can give its classes a default domain. Set the rules with `settings <app>
  'domains+=tools/*=Tools' 'domains+=android/**=Voice'` (`+=` adds, `domains-=tools/*` removes by
  glob, `domains=[json list]` replaces all; no rescan), or edit `settings.json`:
  `"domains": [{"files": "tools/*", "domain": "Tools"}, {"files": "android/**", "domain": "Voice"}]`
  (same globs as `loaded_by`, which `settings` sets the same way: `'loaded_by+=src/plugins/*.py=Registry'`;
  the first match wins). It is a fallback: a `domain` note (AI or
  human) always wins, `curate` counts it as set, and views show `domain=Tools (rule)`.
- Library code inside the repo (a statically linked C runtime, a graphics or sound library, decompiled
  into the same folder as the game): the `library` setting lists rules that mark matching classes as
  outside code, like npm or maven packages: hidden unless `--all` (graph/zoom/view) or the viewer's
  outside-code switch, and they give no `unused`/god/cycle warnings and no hub or level of their own
  to the own code. A rule is a path glob (`out/pseudo/6*.c`), `name:PATTERN`, `tag:library` or
  `domain:Graphics Library`: `settings <app> 'library+=tag:library' 'library+=out/pseudo/6*.c'`
  (`library-=<rule>` removes one). It applies when the map is loaded (no rescan), so tag and domain
  rules work; notes and domains on library classes stay. To mark single classes, tag them
  (`set <app> --file batch.txt`, lines `<id> tags=library`) and use the `tag:library` rule. Check the
  split with `graph <app> 0` (counts) and `--all`. A rule matching own code hides it too: sample first.
- Vendored and minified files (a `vendor/` or `third_party/` folder, `*.min.*`, versioned names
  like `lib-1.2.3.js`, files with a line over 1000 characters) are left out of the map and the
  text index. For other generated or copied code add globs to the app's `settings.json`:
  `"exclude": ["deploy/web-extra/*", "*.generated.js"]` (or `settings <app> exclude=a/*,b.js`);
  it applies at the next rescan.
- The scan reads only files git tracks or does not ignore. To scan chosen git-ignored folders
  (generated or decompiled code), add `"include": ["generated/", "out/pseudo/"]` (folders or
  globs) to `settings.json` or run `settings <app> include=generated/,out/pseudo/`. `exclude`
  and the vendor check still apply on top; it applies at the next rescan.
- C code that a macro turns into functions (`REWRITE(272b4) { ... }`) reads as a function when the
  app's `function_macros` setting names the macro and a name template ({1} = the first argument):
  `settings <app> 'function_macros={"REWRITE": "F_{1}"}'`. Only a macro call at the start of a line,
  followed by `{`, counts. Compiler words tree-sitter-c lacks (`__watcall`, `__far`, ...) are blanked
  before parsing, and decompiler names (`undefined4`, `CONCAT31`, `in`) are built-ins, never unresolved.
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
- `nickname`: A readable name for a class or function whose real name says nothing (decompiled F_243e0, minified a.b); shown as the main name, the real one beside it. One word, no spaces. Max 40 characters.
  - For: Anyone reading code with useless names, such as a decompilation, minified or old code.
  - Not: A rename of a name that is already clear, or a sentence (that is the hint); it never changes the code.
  - Good: `update_world`
  - Bad: `the function that updates the world every frame`
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
- `loaded_by`: The classes that load or start this one by name (a registry, a plugin list, an engine setting), when no code reference shows it.
  - For: Anyone wondering why this class looks unused.
  - Not: Normal code references (the scan finds those) or guesses; only loaders you checked.
  - Good: `coderstanding.language`
  - Bad: `probably main`
- `loaded_how`: One short line on how the loaders find this class. Max 120 characters.
  - For: Anyone following the loaded-by line.
  - Not: How the class works, or a list of loaders (those go in loaded by).
  - Good: `Auto-loaded by the language registry from the languages folder`
  - Bad: `Loaded somehow at startup`
<!-- field-rules:end -->

## Flags and code descriptions
- Entry flags name the mechanism: `godot-main` (root script of `run/main_scene`),
  `godot-main-scene`, `autoload`, `program-main` (C# and Java `Main`/top-level statements, C/C++
  `main`, Kotlin top-level `main` / `@JvmStatic main` / Android `Application`), `script-main` (Python `__main__`, Node shebang or package.json `bin`/`scripts`; a
  tool, level 2 when the app has a start). Every entry also has `entry`.
- A node's `description` falls back to the code's own header comment (`description_by:
  "code"`); your AI drafts and the user's text replace it. Curate only what the code leaves
  unsaid. GDScript scripts without `class_name` are named by file (`main.gd`).
- Calls from a class or module to itself are internal calls: they show when the class is opened, and `impact`/`tests --diff` follow them function by function.

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
