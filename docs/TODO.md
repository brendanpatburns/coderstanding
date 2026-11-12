# TODO

Small things decided but not scheduled. Bigger work lives in `docs/DESIGN.md` §14 Milestones.

- [x] "Open in editor" link (VS Code `vscode://file/<path>:<line>`) and a "View on Forgejo" file
      link in the details panel for classes and functions (DESIGN §12a). `/api/meta` `source`.
- [x] Human note edits record the user's git `user.name` instead of `you` (AI edits keep their
      own name, e.g. `claude`, with `ai: true`). Done with comments: `notes.human_author`.
- [ ] Maybe later: write an accepted description back into the header comment.
- [x] Reader fixes found matching the mock: autoload / typed-field hookups and emits (`Events.hit.emit()`,
      `hit.emit()`, `emit_signal("x")`, own-signal `connect`) now read as `emit` / `signal` refs instead of
      plain calls; `func _init()` bodies are read too (were skipped). Done with Godot loose ends.
- [ ] Polish leftovers: level 2 of hub-heavy apps is a web of hub lines between the
      level-1 band and the level-2 grid; Hub/Grid look sparse at low levels; many self-call
      arcs on big open boxes are busy; outside code with no line at the shown level floats
      where its (hidden) user sits.
- [ ] Found curating a large app (2026-10-29): `curate` lists classes whose code header
      comment already describes them as `-hint` (508 left that way); level 0 domain lines all
      read `~> (via 2)`, so direct vs indirect is lost; level 1 shows long `~> (via 6)` lines
      between core classes; `set` totals count connections with classes.
- [x] `warnings` counts outside imports of tool scripts (`bpy`, stdlib) and model
      assets in scenes (`.glb`) as unresolved; maybe a separate "outside" bucket. Done: `is_outside_name`.
- [ ] Found writing tours (2026-11-11): `tour <app> <id> --note` (and other tour writes) run inside a
      worktree write into the main checkout's `.coderstanding/tours`, not the worktree's; no CLI flag sets a
      step's code range (`--draft` shows 8 lines, so a real tour was hand-edited, then `--still-true`).
- [ ] Layout leftovers (2026-11-11): Coderstanding's own level 5 has +31% crossings from fold-by-users
      (compare both fold orders per band); right-angle routes blocked by the 1.4x detour limit (1.5x? user call).

## Done: progress tracks (approved 2026-11-11, built 2026-11-11; DESIGN 7g)
Generic "how much is done" numbers for any project ("Draft 50% complete", a decomp's "78% proven").
- A track = name + "of" (a set picked with the map's filters: folder, domain, file kind, tag,
  class/function) + "done" (a tag, a note field filled / accepted by a person, or a scanner fact:
  has a test, not a stub, no warnings). Defined per project in `settings.json` `tracks`.
- Project-specific truth stays outside: a project's own script tags items through `set --file`
  (e.g. StarCom tags `proven` from its checker). No project formats in Coderstanding.
- Built-in tracks with no setup: Described (has a hint), Tested (nearest test), Built (uses the
  `planned` branch's shared built predicate: not planned, not a stub).
- Shows: `coderstanding progress <app>` (`Proven 26.9% (67/249) · +3 since last scan`), a bar per
  domain in the overview, a ring per box for the chosen track, a spec's progress = share of its
  linked classes/functions done (so specs read "50% complete"), history per scan for a trend.
- [ ] Maybe later: weigh by size (a decomp counts bytes: StarCom's PROOF_STATUS says 26.9% by bytes,
      27.7% by count); a trend chart beyond the tooltip; Described could count code header docs.

## Later: work tickets

- [x] Hotspots (`coderstanding hotspots`): rank classes by size × git churn × missing tests (checks/testcode data + git log), for when the app gets work-ticket connections.

## Done (layout readability, 2026-11-11)

- [x] Layered layout reads better on real apps (real app, level 2: crossings -18%, lines under boxes
      after bending -49%, mean line length -27%): best-order sweeps, rows lined up, bands slid and
      folded under their users, gap-channel bends, lines in one gap spread apart; numbers from
      `CoderLayout.measure` / `tests/e2e/layout_bench.mjs`.

## Done (m7 browser QA, 2026-10-29)

- [x] Layout: Layered is the default; first-run spots by level (Organic level by level), outside
      code beside its first user, tools in a left column, size-scaled spacing, readable first fit.
- [x] Bends never take long detours (lines ran out to empty points); steep lines get side channels.
- [x] Opening/closing a class moves nothing: the open box grows from its top-left, drawn over others.
- [x] Hub centres on the selected class / function's class / line's from class, else the start;
      the button says "Hub around X"; the centre stays where it is drawn.
- [x] Saving a note no longer re-fits the camera.
- [x] End-to-end checks in headless Chromium (`tests/e2e/run.mjs`).

## Done (m6 viewer polish, 2026-10-29)

- [x] Fan-out polish: labels only on hover/selection in busy views (×N counts stay), busy class
      pairs share a trunk, labels keep off box headers, open boxes don't cover other headers.
- [x] Layout polish: label spots spread around busy hubs, lines that skip a column bend around boxes.
- [x] Start badges (Godot main, Godot main scene, Godot autoload, Program main(), Script __main__)
      on boxes and in the details panel; "from code comment" note on code descriptions.
- [x] Clashing names show enough path; Highlight tags refresh on each view load; Copy for AI
      command keeps the zoom's `--via`; `node` lists at most 8 tested-by files.
- [x] Box faces as in the mock-up, drawn toggle triangle, warning mark, ignore warnings,
      scrolling/filterable function lists, resizable side panels.

## Later: more readers (user, 2026-11-11)
- Shaders (`.gdshader`, `.glsl`): uniforms, functions, includes.
- RimWorld XML Defs: each Def as a node, `ParentName` / `<li Class=>` / defName references as lines.
