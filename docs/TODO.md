# TODO

Small things decided but not scheduled. Bigger work lives in `docs/DESIGN.md` §14 Milestones.

- [ ] Low priority: "Open in editor" button (VS Code `vscode://file/<path>:<line>`) and a
      Forgejo file link in the details panel (DESIGN §12a).
- [ ] Low priority, with git integration: human note edits record the user's git `user.name`
      instead of `you` (AI edits keep their own name, e.g. `claude`, with `ai: true`).
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
- [ ] `warnings` counts outside imports of tool scripts (`bpy`, stdlib) and model
      assets in scenes (`.glb`) as unresolved; maybe a separate "outside" bucket.

## Later: work tickets

- [ ] Hotspots: rank classes by size × git churn × missing tests (checks/testcode data + git log), for when the app gets work-ticket connections.

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
