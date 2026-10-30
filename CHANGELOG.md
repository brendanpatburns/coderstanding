# Changelog

## 0.2.3 (2026-10-30)

- TypeScript projects: far fewer classes that look unconnected. Imports now resolve through
  tsconfig/jsconfig `paths` and `baseUrl` (with `extends`, JSONC comments and trailing commas,
  and `tsconfig.app.json`-style siblings), workspace package names (`@example/shared` -> its
  folder), barrel files (`export * from`, `export { A as B } from`, `export * as ns from`), default
  exports (incl. `export default class { }`), folder `index` files and ESM `./x.js` meaning `./x.ts`.
  The JS/TS reader adds JSX components (`<UserCard/>`, `<Ns.Item/>`), class decorators and the
  classes listed in them (Angular/Nest `@NgModule`/`@Module`/`@Component({imports})`), `inject(X)`
  fields, `readonly` parameter properties, `implements Foo<T>`, type arguments, `as` casts and other
  type positions, interface members, enums, lazy `import('./x')`, and `.jsx`/`.mts`/`.cts` files.
  An imported class counts as used by the importing file.
- `coderstanding diagnose <app> [--examples]`: a privacy-safe summary of why things are unconnected
  (unresolved import patterns, tsconfig counts, unresolved names), safe to paste into a public issue.
  `warnings` points to it when imports found no file. Scanner version 11 (rescan).
- Java reader (`*.java`): types, nested types, members with line ranges, field/parameter/return/
  generic type refs, calls, imports (single, `.*`, static), `main` entry; Maven `pom.xml` in the
  stack reader (`maven` dependencies mapped from imports by groupId).
- Kotlin reader (`*.kt`, `*.kts`): types, companions, supertypes, constructor-injected and
  delegated properties, calls, imports/aliases, extension functions, entry points; Gradle build
  files feed the stack (Kotlin/Android plugin as stack name, dependencies as `maven`, modules).
  Needs `tree-sitter-kotlin`.
- Field rules: every note field now says who it is for, what goes in, what does not, with a good and a bad
  example. `hint` and `description` are for a non-technical newcomer (what it is FOR, not how the code works).
  Shown in the viewer's "?" tips, the AI guide ("Writing note fields"), `set --help` and `curate`; `set`
  reminds an AI (never refuses) about long or code-like hints. Teams override any part, set the hint length
  and add house rules per project: `coderstanding fields`, the details panel's "Edit field rules", or
  `fields.md` in the project data folder (`GET/PUT /api/fields`).

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
