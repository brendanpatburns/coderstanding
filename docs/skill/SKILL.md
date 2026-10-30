---
name: coderstanding
description: Read a code base's structure cheaply before opening files. Use when starting work in, exploring, planning or briefing agents on a repo registered in Coderstanding (~/.config/coderstanding/apps.json), or when the user asks to register/scan a repo or mentions Coderstanding.
---

# Coderstanding

`coderstanding` (on PATH) prints a repo's class graph as slim text, so you read structure
instead of grepping files.

1. Check the app name: `cat ~/.config/coderstanding/apps.json`. Not registered yet?
   `coderstanding add <name> "<repo path>" --data ~/.local/share/coderstanding/<name>`.
2. Start with `coderstanding graph <app> 1`, then `node`, `zoom`, `tests`, `warnings`.
3. Know a name or topic, not where it lives: `coderstanding find <app> <words...>`.
   About to read a class: `coderstanding outline <app> <Name>` first, then read only the
   `file:first-last` range you need. About to change a class or function (`Name#func`):
   `coderstanding impact <app> <target>` for dependents and the tests to rerun. Briefing
   another agent: paste `coderstanding brief <app> <targets...>`.
4. Learning how a flow works (or explaining it to the user): `coderstanding tours <app>`
   lists guided tours; `coderstanding tour <app> <id>` prints each step's text and only its
   code lines (`--step N` one step, `--marked` just the highlighted lines). Asked to explain a
   flow, write one: `tour <app> <id> --draft "Title" A#f A>B ...` (an AI draft the user accepts),
   then fix steps with `--step N --text ...`; `(changed)` steps need re-checking.
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
   Paste any `rules X:` lines it prints for classes the agent touches.

Full reference (how to read the text, curating with `set`): read `FOR_CLAUDE.md` in this
skill's folder (`coderstanding skill install` puts it there; in a checkout where this folder is
linked into `~/.claude/skills`, it is `../FOR_CLAUDE.md`).
