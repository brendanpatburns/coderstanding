# Coderstanding: design

Status: design agreed 2026-10-29; building milestone 1.

## 1. Goal

A local tool that scans a code base and shows its classes and how they connect, as a graph
you can read at different levels of detail.

- A **scanner** script reads a repo and writes a graph file.
- A **viewer** (local web page) draws the graph.
- A **granularity slider** hides detail classes. When a class is hidden, connections that
  pass through it still show, as one connection between the visible classes on each end.
- Every connection is **directional** and can carry **your own text** explaining what it is.
  This matters most for the high-level connections.
- Two **views**: *All types* (classes and interfaces) and *Concrete* (only concrete classes;
  connections through an interface read "as IFoo").

- **Zoom**: pick a connection and see only its two ends plus one level more detail
  between them; connections leaving that set show faded at the edge.
- **Specs**: a collapsible sidebar of specs in nested boxes (epics and sub-epics). Specs can
  have classes attached, and both you and Claude can search them.
- **Stack & deps tab**: the tech stack (languages, engine, runtimes, build tools) and every
  external dependency, with which classes use it.
- **Slim text output** for Claude: `coderstanding graph mygame 1` prints the top level in a few
  lines; `coderstanding zoom mygame GameEngine VFX` drills into one connection. Claude reads the
  structure without opening files, which saves tokens. The viewer and the CLI show exactly the
  same views because they share one view engine.

C# and Godot (GDScript, C# Godot scripts, scenes, `project.godot`) are supported from the
first version, plus Python and JavaScript/TypeScript so Coderstanding can scan itself, and
C/C++ and HTML so it can scan large C/C++ code bases.

**Test grounds**: Coderstanding itself (Python + JS), a Godot game (GDScript, ~200 scripts) and
an addon for it kept in another repo (GDScript). C# has no real repo yet, so it is tested on small
made-up fixtures.

Example: `GameEngine` uses `Viewport`, which uses `Renderer`, which pushes to `IEffectSink`,
which `VFX` implements. At the top level only `GameEngine` and `VFX` show, with one hollow (outlined)
connection `GameEngine ⇢ VFX` "via Viewport, Renderer, IEffectSink", and you can write
"drives rendering, which triggers effects" on it.

## 2. Concepts

**Node**: one type. Kinds: `class`, `interface`, `struct`, `record`, `enum`, `script`
(a GDScript file with no `class_name`), `inner` (inner/nested class), `module` (a Python or
JS/TS file; its top-level functions are its members, and its classes are `composition`
children of it), `scene`, `external`. Flags: `abstract`,
`static`, `singleton`, `autoload`, `partial`.

**Edge**: a directed connection `from → to`. Its `kind` uses the standard UML relationship
names, and `detail` says how the code makes it:

| kind | meaning | sources | detail |
|---|---|---|---|
| `generalization` | inherits from a base class | C# `: Base`, GDScript `extends` | |
| `realization` | implements an interface | C# `: IFoo` | |
| `composition` | owns the part; the part lives and dies with it | field set by `new` in the owner, `.tscn` child node / instanced sub-scene | `field`, `scene-child` |
| `association` | holds a lasting reference it did not create | field/property/`var` of that type, `@export` | `field`, `export` |
| `dependency` | uses it without holding it | parameter, return, local, generic arg, static or autoload call, `new` not stored, `preload`/`load` | `call`, `create`, `param`, `return`, `local`, `load` |
| `signal` | listens to its signals/events (Godot signal, C# event; not a UML kind) | `.tscn [connection]`, `x.sig.connect()`, C# `+=` | signal name |

Calls on a held field add `call` to that `association`/`composition` edge's details rather
than making a second edge. UML **aggregation** (shared whole-part) can't be told apart from
association by reading code, so the scanner never emits it; you can set it on an edge in notes.

Several edges of different kinds between the same pair are merged into one drawn connection
that lists its kinds. **Calls** are kept too: every member one class calls on another
(`push_effect`, `clear_effects`, `JsonConvert.DeserializeObject`, signal → handler), with a
count and code locations. Many calls are still one line; the line shows the first few names
and the details panel lists them all. Each edge keeps its source locations (file:line) so the viewer can show
"where in the code".

**Level**: an integer per node, 1 = top level. The slider shows nodes with
`level <= slider`. The scanner guesses levels; you override them.

**Note**: your additions: per node and per edge a short **hint** (shown on the line) and an
optional long **description** (shown in the details panel), plus level overrides, interface
bindings, singleton flags, and domains.

**Outside node**: one node per external package or addon (section 8), drawn as a rounded
box. Its lines in and out carry calls like any other line.

**Domain**: a named card grouping classes into a broad area ("Engine", "Networking",
"Audio"). Not a folder tree, just the general idea. See section 6b.

**View**: what you see for a given (level, all/concrete, optional zoom). Computed by the
**view engine** from graph + notes. Every consumer (viewer, text CLI) asks the engine; none
computes views itself.

## 3. Architecture

```
repo ──scan──> graph.json ─┐                 ┌─> server ──JSON──> viewer (browser) ──edits──> notes.jsonl
                           ├─> view engine ──┤
           notes.jsonl ───┘                 └─> text CLI ──slim text──> you / Claude
```

- **Scanner**: Python 3 CLI, parsing with tree-sitter (`tree-sitter`, `tree-sitter-c-sharp`,
  `tree-sitter-gdscript`). Writes `graph.json`. Never writes notes.
- **View engine**: one Python module. Takes graph + notes + (level, view, zoom) and returns
  the visible nodes, direct and derived edges, faded outside edges, and hints. This is the
  only place that logic lives.
- **Server**: `coderstanding serve <app> [--port 8517] [--data DIR]` scans, then serves the
  viewer on 127.0.0.1. Routes: `GET /api/view?level=&view=&scene=&external=&test=&zoom_from=&zoom_to=&zoom_level=&via=&expand=`
  (engine output as JSON; `via` and `expand` repeat; `expand` opens classes into functions, `*` = all, adds
  `expanded` and `fanout`, section 12c; `test=1` shows test code, default 0), `GET /api/text?<same>&select=<id|A→B|id#fn>`
  (plain slim text of that view, first line the `coderstanding view` command, section 11), `GET /api/node/<id>`, `GET /api/stack`,
  `GET /api/meta` (levels, statuses, tags in use, warning help texts),
  `GET/PUT /api/layout`, `PUT /api/notes` (`{key, fields:{hint,description,status,tags,rules,review}}`, written as `you` (human), empty string clears, tags as a list or `a, b` text, returns the merged note), `POST /api/notes/accept` (`{key, fields:[...]}` turns AI drafts into yours); keys are `node:<id>` / `node:<id>#<function>` / `edge:<from>→<to>`, 400 if they name nothing in the graph. Python stdlib
  `http.server`, no deps. A slider move is one local request, fast enough.
- **Viewer**: static HTML/JS, Cytoscape.js with the ELK layered layout (left-to-right flow
  suits directed graphs). Draws what the engine returns; no graph logic of its own.
- **Text CLI**: prints engine output in the slim format (section 9).

Files live in the scanned repo under `.coderstanding/`:
- `graph.json`: generated, safe to delete, should be gitignored in that repo.
- `notes.jsonl`, `layout.json`, `specs/*.md`: yours, meant to be committed with the code so
  hints, levels, layout and specs travel with it. All are one item per line (or file) and
  sorted, so parallel branches merge cleanly.

### Why tree-sitter over Roslyn

Roslyn would resolve C# types exactly, but it is C# only, needs the project to restore and
build, and does nothing for GDScript. Tree-sitter parses every language the same way, works on
broken or half-written code, and needs no build. We resolve type names ourselves (section 4.1),
which is enough because we only care about types defined inside the repo. If C# resolution
turns out too weak on real projects, a Roslyn-based C# reader can replace the C# language
module later without touching anything else.

## 4. Scanner

```
coderstanding add <app> <path>        register a repo under a short name
coderstanding scan <app|path>
coderstanding serve <app> [--port 8517]
coderstanding graph <app> [level] [--concrete]
coderstanding zoom <app> <from> <to> [--concrete]
coderstanding node <app> <name> [level]
coderstanding specs <app> [level | query] [--zoom <id>]
coderstanding spec <app> <id>
coderstanding deps <app> [name]
coderstanding curate <app>            classes needing levels/domains/hints, compact
coderstanding set <app> <id> key=value ... | --file <batch>
```

Registered apps live in `~/.config/coderstanding/apps.json` (name → path). **Manual refresh only**
(decided 2026-10-30 by the user): nothing scans by itself. Text commands and the server read the
saved `graph.json`; a project is scanned only by the viewer's Refresh (`POST /api/rescan`), by
`coderstanding rescan <app>`, or when it is added in the viewer. A project with no graph says "never
scanned: press Refresh"; one written by an older scanner still loads with a note ("made by an older scanner: press
Refresh"; only an unknown graph.json format refuses). `coderstanding add` does the first scan. The server only
`stat`s `graph.json` per request to pick up a finished rescan; it never walks the repo, so the
helper (which has no console on Windows) starts `git` only for a scan. Every child process
(git, the rescan child, taskkill) starts through `procs.py` so no console window opens.
The helper is a single instance: it binds its port exclusively, `serve` exits if a helper
already answers, and `helper install|start` start none when one does.

Layout: one module per language, all producing the same model; shared steps are their own
modules (see `docs/COMPONENTS.md` once built).

1. Find files (`*.cs`, `*.gd`, `*.tscn`, `project.godot`, `*.py`, `*.js`/`*.ts`,
   `*.c`/`*.h`/`*.cc`/`*.cpp`/`*.cxx`/`*.hh`/`*.hpp`, `*.html`), skipping `.godot/`, `bin/`, `obj/`,
   `addons/` (configurable), and anything gitignored.
2. Each language module parses its files into **declarations** (types with kind, flags,
   namespace, base list, members with type names) and **raw references** (type name + edge
   kind + location).
3. **Resolver** (shared) turns raw type names into node ids.
4. **Godot project reader** adds autoloads, main scene, scene edges.
5. **Stack reader** collects tech stack and external dependencies (section 8).
6. **Level guesser** makes a rough first guess (section 4.5).
7. Write `graph.json` (nodes, edges, and a `stack` block).

### 4.0 Speed on big repos

- **Per-file cache**: each file's parse result (declarations + raw refs) is cached in
  `.coderstanding/cache/` keyed by path + content hash + scanner version. A rescan only
  re-parses changed files, then re-runs resolve/levels (cheap, whole-graph). Text commands
  rescan automatically, so this keeps them fast.
- **Parallel first scan**: files are parsed in a process pool (one worker per core).
- **Viewer cap**: past ~1500 visible boxes the viewer asks you to raise the level or zoom
  instead of drawing everything.

### 4.1 Name resolution (shared)

- Only types declared in the repo become nodes. References to outside types (`System.*`,
  Godot API like `Node3D`) are not nodes; they go to the stack reader (section 8), and the base class name is kept on the node as
  info (`extends Node3D`).
- C#: resolve a name by current namespace and parents, then `using` directives, then
  global. Partial classes across files merge into one node. Nested types get ids
  `Outer.Inner`.
- GDScript: `class_name` names are global. Path references (`extends "res://x.gd"`,
  `preload("res://x.gd")`) resolve by path. Autoload names resolve to the autoload's script.
- A class referring to itself (own-function calls, a field of its own type) keeps that as an
  edge from the node to itself. Views, levels and warnings ignore self edges; the function
  fan-out shows their calls inside an open class. An inheritance self reference is dropped.
- Ambiguous name → edge kept, marked `"unsure": true`, viewer marks it with a `?`.

### 4.2 C# (plain and Godot)

- Declarations: class, interface, struct, record, enum, nested types; `abstract`, `static`,
  `partial`.
- Edges: base list (`generalization` / `realization`, decided by whether the target is an
  interface), field and property types (`composition` when the owner assigns `new T()` to
  it, else `association`), member calls through a field (detail `call`), parameter/return/
  local/generic types, static calls, and `new T()` not stored (`dependency`),
  `event += handler` (`signal`).
- Singleton hint: `public static T Instance` (or `Singleton`) on type `T`.
- Godot C#: `GetNode<T>()` → `uses T`, `[Signal]` delegates listed on the node,
  `GD.Load<PackedScene>("res://...")` → `dependency` (detail `load`).

### 4.2b Java

- Declarations: class, interface, enum, record, annotation type (kind `interface`), nested types
  (`inner` for classes); ids are `package.Class` and `package.Outer.Inner` (`model.child_id`),
  the scheme the Kotlin reader shares, so mixed Java/Kotlin projects link through the shared
  resolver with no language-specific code. Methods, constructors and fields are members (methods
  with start/end lines). Anonymous classes and lambdas count as their enclosing method.
- Edges follow C#: `extends`/`implements` (`generalization`, `realization` when the target is an
  interface), field types (`composition` for `new T()` or assignment of one, else `association`),
  one ref per type name in parameters, returns, `throws`, generic arguments, locals, casts and
  annotations (arrays, generics, wildcards stripped; `java.lang` names skipped), `new T()`, calls
  through a held field (detail `call`) and static calls `T.m()`. Every ref has `from_member`
  (`<field>`, `<init>` for initializers and static blocks).
- Imports: a single-type import is a file alias, `pkg.*` and `import static pkg.T.*` are
  import prefixes, `import static pkg.T.m` makes an unqualified `m()` a call on `T`. Same-package
  names resolve through the package scope chain, as C# namespaces do. Unresolved imports stay
  outside names (mapped to a Maven dependency by groupId prefix; `java.*`/`javax.*` are the JDK).
- Entry: `public static void main` gives `program-main`. Spring annotations are plain
  `annotation` dependencies (no flag): `@Autowired` fields and constructor parameter types are
  already ordinary refs.

### 4.3 GDScript

- One node per `.gd` file: id = `class_name` if set, else the `res://` path; name = class
  name or the file name with its extension (`main.gd`, so it differs from the scene `main.tscn`;
  `coderstanding node main` still finds both). Inner `class X:` → `inner` node.
- Edges: `extends` (name or path, `generalization`), typed `var x: Foo` / `@export var x: Foo`
  (`composition` if the script does `x = Foo.new()`, else `association`), `Foo.new()` not
  stored, `Foo.static_func()`, autoload calls, param/return hints, `preload`/`load` of
  `.gd`/`.tscn` (`dependency`), `x.some_signal.connect(...)` (`signal`, when `x`'s type is
  known).
- **Preload names** (most important for the test game, which has 1 `class_name` but 384 of these):
  `const Acoustics := preload("res://src/audio/acoustics.gd")` makes `Acoustics` a local name
  for that script, so `Acoustics.new()`, `var a: Acoustics` and `Acoustics.foo()` resolve.
  Same for `load(...)` assigned to a const or var.
- **Inferred types**: `var x := Foo.new()` and `var x := preload(...).new()` give `x` the type
  `Foo`, so later `x.bar()` counts as a call on `Foo` (the test game has ~900 of these).
- Untyped code gives fewer edges. That is accepted; scenes fill a lot of the gap.

### 4.4 Godot project and scenes

- `project.godot`: `[autoload]` entries → node flags `autoload` + `singleton`; the main scene
  gets `godot-main-scene` and its root script `godot-main`.
- **Entry flags** name the mechanism (`model.ENTRY_MECHANISMS`), and all of them also carry
  `entry`: `godot-main`, `godot-main-scene`, `autoload`, `program-main` (C# `static Main` or
  top-level statements, C/C++ `main`) and `script-main` (Python `if __name__ == "__main__"`,
  a Node file with a shebang or named in package.json `bin` / `node x.js` scripts). The first
  four start the app; `script-main` is a tool.
- **Descriptions from the code**: each reader takes the file's or class's header comment
  (GDScript `##`, Python docstring, C# `/// <summary>`, JSDoc / C++ `/** */`; for a file also
  a leading plain `#`/`//` block, skipping shebang and license text), cut to the first
  paragraph, 600 characters (`languages/doc_text.py`). It is stored as node `doc` and shown
  as the description when nobody wrote one: human > AI > code (`description_by: "code"`).
- **Scenes are nodes** (kind `scene`, id = `res://` path, drawn with their own shape):
  - scene → each script attached to its nodes, and → each instanced sub-scene:
    `composition` (detail `scene-child`).
  - a script that `preload`s / `load`s / `instantiate()`s a scene → that scene: `dependency`
    (detail `load` / `create`).
  - `[connection signal=... from=... to=... method=...]` → `signal` edge between the two
    nodes' scripts (auto-text "signal `hit` → `_on_hit`").
  - Scenes take part in levels and folding like classes; the main scene is a level-1 hint.
- Scenes can be switched off with the type toggles (section 12); then they fold into their
  owners like any hidden node.

### 4.4b Python and JavaScript/TypeScript

Needed to scan Coderstanding itself. Same declarations/references model as the others.

- **Python**: one `module` node per file (id = dotted module path), classes inside it.
  Edges: base classes (`generalization`; `Protocol`/ABC bases → `realization`),
  `self.x = Foo()` in `__init__` (`composition`), `self.x: Foo` / annotated attributes
  (`association`), calls to imported functions/classes and type hints (`dependency`),
  `import`/`from … import` of a repo module (`dependency`, detail `import`). Third-party
  imports go to the stack reader as outside code.
- **JavaScript/TypeScript**: one `module` node per file (id = repo-relative path), classes
  inside it. Edges: `extends` / `implements` (TS), `this.x = new Foo()` (`composition`),
  typed fields (`association`), calls to imported names and `new Foo()` not stored
  (`dependency`), `import`/`require` of a repo file (`dependency`, detail `import`),
  `addEventListener`/`on(...)` with a known target (`signal`). npm packages → outside code.
- Plain-JS code with few classes is mostly module-to-module lines with call lists; that is
  fine and still what you need to see.
- **TypeScript projects** (built 2026-10-30, after a report of many unconnected classes):
  - Reader: each named/default import is also a `dependency`/`import` ref from the module to
    that name (an imported class is used); JSX elements (`dependency`, detail `jsx`, call = the
    component); class decorators and the known names in their arguments (detail `decorator`:
    Angular/Nest modules list the classes they use there); constructor parameter properties
    (`private`/`readonly`/`override`) and `x = inject(Foo)` fields are held fields like C#
    fields; any other type position with a known name (type arguments, `as`, locals, type
    aliases, interface bodies) is a `dependency` with detail `type`. Type aliases and type
    parameters are no nodes and make no refs; enums are `enum` nodes. `export default class {}`
    is the node `<file>.default`, named after the file.
  - Exports (`FileResult.exports`, `star_exports`): re-exports and the default export as names
    a file node offers. The shared resolver follows them when a `Target(path, member)` asks for
    a name that is not a child (barrels, chains, loops end); `import X` asks for `default`;
    exported functions/consts/types point at their own module.
  - Paths: `LanguageModule.read_path_map` runs once per scan; the JS module returns
    `tsconfig.read_path_map`: per folder, its tsconfig/jsconfig files (`tsconfig.json` first,
    then siblings like `tsconfig.app.json`), `extends` applied (a child's `paths` replace the
    parent's; `baseUrl` is relative to the config that set it; `paths` without `baseUrl` are
    relative to their config), and package.json names of workspace packages (entry from
    `source`/`types`/`main`, built `dist/` mapped to `src/`, else `src/index`). The nearest
    folder with configs applies to a file. The resolver tries the path map's bases (best
    `paths` pattern = longest prefix, then `baseUrl`, then packages), then the path as written,
    each with the module's `path_suffixes`, then again without the written extension.
  - `coderstanding diagnose`: the resolver keeps every path no file matched; the scan sorts them
    into reasons (alias marker with no `paths` match, alias matched but no file, repo folder
    name without `baseUrl`, folder without index, missing relative file, package in no
    package.json) into `graph.json` `diagnostics`; the command prints counts only (no app name,
    names, paths or code), `--examples` adds anonymised examples (names -> letters).

### 4.4c C/C++ and HTML

- **C/C++** (`languages/cpp.py`): one `module` node per header/source pair (`foo.h` +
  `foo.cc` in one folder -> id `dir/foo`, the path without extension). `.c` uses the C
  grammar, everything else C++; a `.h` falls back to C when only C parses it cleanly.
  Classes/structs/unions/enums: header types get namespace-qualified ids (`engine.geo.Mesh`);
  source-file types are file-local (`dir/foo.Name`) but still found from other files by
  name (opaque `struct Foo;` in a header, body in a `.cc`), except inside `namespace {`.
  Nested types are `inner`; pure virtuals make a class `abstract`; only pure virtuals and no
  fields make it an `interface`. Edges: bases (`generalization`/`realization`), fields by
  value or `unique_ptr<T>` (`composition`), `T*`, `T&`, `shared_ptr<T>` (`association`),
  params/returns/locals/`new T`/`make_unique<T>` not stored (`dependency`), calls through
  fields and typed variables, free-function calls to the module whose header declares the
  function (headers register `ns.func` as global aliases; the resolver's scope chain finds
  them), `#include "x.h"` (beside the file, from the repo root, or by path suffix as an
  include path would) as `dependency` detail `import`; `<...>` includes are outside names.
  `using a::Foo;`, `using V = T<...>;` and `typedef` become aliases. Out-of-line
  `Foo::bar` bodies attach to `Foo` (id guessed from the enclosing namespace). Template
  args are stripped; macros are ignored (ALL_CAPS calls and names `#define`d in the file).
- **HTML** (`languages/html.py`): one `module` node per page; `<script src>` and
  `<link href>` to repo files are `dependency` detail `import`. Inline scripts are ignored.

### 4.4d Kotlin

`languages/kotlin.py` reads `*.kt` and `*.kts` (tree-sitter-kotlin). Ids are full names like
Java's: `com.example.ChatService`, nested `Outer.Inner`, a companion `Outer.Companion`
(or its own name), so a Java reader links to it through the shared resolver with no
language-specific resolution: Java interop works through shared ids once both readers exist.

- **Nodes:** class, interface (also `fun`/`sealed interface`), enum, `data`/`value class`
  (kind `record`), `object` (flag `singleton`), `companion object` (kind `inner`, flag `static`),
  nested and `inner class` (kind `inner`), `sealed`/`abstract` (flag `abstract`). Top-level
  functions and properties live in one `module` node per file, `<package>.<File>Kt`
  (Kotlin's own class name for them). `typealias` becomes a file alias.
- **Members:** functions (`method`, or `function` on the module node) with first and last line,
  properties (`property`), constructor `val`/`var`.
- **Edges:** supertypes after `:` (the resolver picks generalization or realization); property
  and `val`/`var` constructor parameter types are held fields (`composition` when the class
  creates the object itself: `= Foo()`, `by lazy { Foo() }`, `x = Foo()`; else `association`,
  e.g. constructor injection); a plain constructor parameter, function parameters, returns,
  local types, casts, `Foo()` calls and `Util.helper()` / `Color.RED` uses are `dependency`;
  calls through a held field are `association` detail `call`. Untyped properties take the type
  of `= Foo()` or of `by inject<Foo>()` / `get<Foo>()`. `fun Foo.bar()` gives a dependency
  (detail `extension`) from the declaring type or file to `Foo`. Every ref carries its
  `from_member` (`<init>` for `init` blocks, initializers and enum entries).
- **Imports:** `import a.b.C` and `import a.b.C as D` are file aliases, `import a.b.*` an import
  prefix. Same-package types resolve through the scope chain. Unresolved ones are outside
  refs (a Gradle `group:artifact` dependency claims names by its group, or its artifact read as a
  package); `kotlin.*`, `java.*`, `javax.*` and the default collection names are standard.
- **Entry:** top-level `fun main` is `program-main`, as is a class with `@JvmStatic fun main`
  and an Android `Application` subclass (Activities are not flagged: the manifest decides).
  `*.kts` scripts are `script-main`; `*.gradle.kts` build scripts are left to the stack reader.
- **Limits:** the grammar needs real statement breaks, so a one-line `class A { fun f() {} }`
  can parse as an error and the rest of that declaration is lost. Calls through a variable
  whose type is only inferred from another call are not followed. Built-in types (`Int`,
  `String`, `Unit`, `Any`) are dropped; other stdlib names are emitted like any outside name.
  Java classes a Kotlin file names resolve to the Java reader's nodes through the shared
  ids (and the reverse); Kotlin and Java deps share one `maven` kind.

### 4.5 Levels: a rough guess, then Claude sorts them

Levels are mostly set by Claude, not by a clever heuristic. The scanner only makes a rough
first guess so a fresh scan is usable:

1. Level 1: app starts: autoloads, singletons, the main scene and its root script, program
   mains. Tools (`script-main`: standalone scripts like `make_fonts.py`) start one step down
   (level 2, their parts below) so level 1 shows how the app starts; if nothing else is an
   entry, the tools are level 1.
2. **Hubs**: a class with at least `hub_threshold` distinct neighbours (the same count as the
   god-class check: both directions, no tests, outside code, nesting or self lines) is level 1,
   however it is held, so design mistakes show at the top. It gets the node flag `hub` and
   `hub_degree` (shown as `[hub 80]`; the viewer can say why it is there). Setting: `auto`
   (default, = the god-class threshold, max(20, 6 x median)), a number, or `off`/`0`. Chosen per
   app before scanning: `<data>/settings.json`, set by `add --hub-threshold`, `settings <app>
   hub_threshold=N`, or one-off `scan --hub-threshold`. graph.json records the settings used
   (`settings`, `hubs`); new settings apply at the next rescan (levels are cache-free, so
   only the cheap whole-graph stages rerun). Notes level overrides still win.
3. Others: 1 + shortest distance from a level-1 node along `composition`/`association`,
   capped at the max level (default 5).

Then a **curation pass** by Claude (on first scan, and for new classes after rescans):

- `coderstanding curate <app>` prints a compact table for every class not yet curated: id,
  file, current level, lines in/out, top calls, folder. Small enough to read in one go.
- Claude answers with one batch: `coderstanding set <app> --file levels.txt` where each line
  is `Renderer level=3 domain=Rendering hint="..."`. Same command sets domains, hints and
  descriptions, so one pass can level, group and describe.
- Every value records who set it: a real author name in `by` (`you` from the viewer, `claude`
  for Claude's writes) plus `ai: true` on AI drafts. Human values always win; an AI write never
  overwrites them, and the viewer shows AI values as "AI draft" (author on hover) you can accept
  or change. Old lines without `ai` count as AI when `by` is `claude`.
- The same pass is how Claude drafts the high-level hints and descriptions.

## 5. Collapsing hidden nodes (derived edges)

Part of the view engine. For the current slider value a node is visible if
`level <= slider`. Every hidden node **folds into a visible node** that stands for it, and its
edges move with it:

1. A hidden class folds into its **owner**: the source of its incoming `composition` edge,
   else `association`, else `dependency`; ties go to the owner with the higher level (smaller
   number). If the owner is hidden too, keep going up.
2. A hidden **interface** folds into its bound implementations (section 9).
3. A hidden class with **no owner** passes its incoming lines forward to what it reaches.
4. Each edge `a → b` is redrawn between the visible stand-ins `A → B`. If `A == B` the edge is
   internal and is dropped. Otherwise it becomes a **derived edge**, remembering the hidden
   classes on the way ("via Viewport, Renderer") and the calls on each hop.

Example: `GameEngine` owns `Viewport`, which owns `Renderer`; `Renderer` calls `IEffectSink`,
bound to `VFX`. At level 1, `Viewport` and `Renderer` fold into `GameEngine`, so the call
shows as `GameEngine ⇢ VFX` via Viewport, Renderer. A camera addon's signal into `Viewport`
shows as `camera ⇢ GameEngine`, not pushed on to whatever Viewport uses.

- All edges between the same `A → B` merge into one, drawn as a hollow outlined line; the
  details panel lists every path and the calls on each hop.
- `generalization`/`realization` edges are only drawn when both ends are visible.
- Notes key edge hints by the pair `A→B`, the same for direct and derived edges, so a hint
  written at level 1 stays put when you slide down and back.

Sizes of a few thousand nodes should be fine to recompute per slider move; cache per level
if not.

## 6. Zoom

Zoom drills into one connection `A → B` seen at level `L`.

- **Shown**: `A`, `B`, and the hidden classes that derived edge went through (its "via"
  list) whose level is at most `L + 1`. Deeper ones stay folded by the section 5 rule. So a
  zoom shows exactly one more level of detail.
- **Faded outside edges**: any edge from a shown node to a node outside the zoom set is drawn
  faded, ending in a stub labelled with the outside node's name (collapsed to its visible
  end at level `L`). Nothing is lost; you can click a faded edge to zoom into that instead.
- **Everything else is gone**: an outside class only appears (faded) when a faded edge ties
  it to the zoom set. Classes with no connection to the zoom are not drawn at all.
- **Drilling**: clicking a connection inside a zoom zooms again (level `L + 2`, and so on).
  The viewer keeps a breadcrumb trail (`GameEngine→VFX › Viewport→VFX`) to step back.
- **Node zoom**: zooming on a single node shows it, its direct neighbours at level `L + 1`,
  and faded edges beyond them.
- If a path between `A` and `B` only has nodes deeper than `L + 1`, the zoom shows the
  derived edge with its hop count and you drill again.

## 6b. Domains

Domains are cards on the main graph that group classes into broad areas: "Engine",
"Networking", "UI", "Audio". They answer "what part of the app is this?" without showing the
folder tree.

- **Suggested, not shown**: the scanner proposes domains from top folders (`src/net/` →
  "Net") but they stay hidden until you accept, rename, merge or drop them. You can also make
  domains by hand and drag classes into them. Stored in notes.
- One domain per class. A class with none is fine.
- **Drawn as cards** behind their classes, with the domain name on the card.
- **Domain level**: an extra step at the top of the slider (level 0). Each domain becomes one
  card and lines between domains merge all the calls between their classes, by the same fold
  rule as section 5 (a class folds into its domain). Zoom into a domain-to-domain line to see
  the classes on each side.
- Text: `coderstanding graph mygame 0` prints domain-to-domain lines.

## 7. Specs (built 2026-10-30, with full add/edit in the viewer)

Specs are the plan for the app, organized like epics: top-level specs hold sub-specs, which
hold their own, as deep as needed. They live in a collapsible **sidebar** in the viewer,
drawn as nested boxes, not as a graph.

**Built (2026-10-30)**: `specs.py` on `items.py` (since round 2: `cards.py`, see 7a, which also
holds notes as memos, stages, priorities and two-way links), CLI `specs` / `spec`, the API below,
and the sidebar (`viewer/spec_view.js`). The user asked to add and edit specs in the viewer too, so the
sidebar is a full editor. Decisions from the build:
- **Folder**: `<data>/specs/<id>.md`, the same rule as tours (the app's data folder; that is the
  repo's `.coderstanding/` unless the app keeps its data elsewhere, e.g. `--data`).
- **File** (round 2 names, 7a): `id, title, parent?, level?, stage?, priority?, importance?,
  order?, links, by, ai?`, body = text (older files with `status` / `classes` still read). `level`
  is stored only when set (default parent + 1); `order` sorts siblings (unset: after the ordered
  ones, by title). Links are CLI targets; the viewer's ids are
  stored as the short name when that is unique.
- **Authors, per field** (notes model): `by` = the spec's author, `ai` = the fields still AI drafts
  (`ai: true` = all; a file with `by: claude` and no `ai` counts as all AI). A human edit makes the
  changed fields the human's (fields sent back unchanged keep their author); Accept makes all
  (or the named) AI fields the human's. An AI write never changes a field a human filled in; the
  CLI says `kept your <field>`.
- **Ids** come from the title once (`slug`, `-2` when taken) and never change on rename.
- **Checks**: a write is refused for a missing parent, a parent cycle, or a set level shallower
  than the parent's + 1. Hand-edited files that break a rule still show (a lost parent or cycle
  at the top; a too-shallow level shows one finer than its parent) and are listed as problems,
  as are unknown classes and unreadable files (never dropped or overwritten). `warnings` prints
  them as `spec problem:` lines.
- **Move**: a spec moved under a parent its set level no longer fits drops the set level
  (follows the new parent). Re-parenting and reordering use `parent` + `position` on PUT.
- **Delete moves the children up** to the deleted spec's parent, at its place (not refused): the
  delete confirm says so.
- **Folding is computed by the server** (`Specs.tree`): top specs always show; with a level,
  finer specs fold into their nearest shown parent (`finer` count, `folded_classes`, and `focus`
  = own + folded classes as view ids). Zoom shows the spec and its direct children (one tree
  level more, so a child that skips levels still shows), with breadcrumbs.
- **API and viewer**: see 7a (one router and one sidebar for specs and notes).

- **Levels like the graph**: every spec has a level, 1 = a broad area ("Rendering"), up to
  the finest ("Player has a turret"). Default is parent + 1; you can set it deeper to skip
  levels, never shallower than its parent. The sidebar has its own detail slider, or a
  "follow graph" switch that ties it to the graph slider.
- **Folding**: specs finer than the slider fold into their visible parent. The parent box shows
  "+N finer" and the classes of the folded specs as outlined chips (outline = hidden, same as
  the graph). Selecting the parent highlights its own and its folded specs' classes.
- **Zoom**: "+N finer" zooms into that spec: it and one level more, with breadcrumbs back.
- **Search ignores levels**: it finds specs at any level and shows where each one sits
  ("Rendering › Effects pipeline › Hits throw sparks").
- **Attach classes**: a spec lists the classes it covers. Selecting a spec works like a zoom:
  its classes are highlighted, classes one connection away show faded, everything else is
  hidden. If a class is hidden at the current level, the visible node or
  derived edge it collapsed into is highlighted instead, so a spec about `Renderer` still
  lights up `GameEngine ⇢ VFX` at level 1. Clicking a class lists its specs.
- **Search** (you and Claude): titles, text, and attached class names. Sidebar search box,
  and `coderstanding specs <app> [query]` on the CLI.
- **Status** per spec: `idea`, `planned`, `doing`, `done`, `dropped`, shown as a chip.
- **Storage**: one Markdown file per spec in the scanned repo, `.coderstanding/specs/`,
  committed with the code. Front matter holds the structure, the body is free text:

  ```markdown
  ---
  id: effects-pipeline
  title: Effects pipeline
  parent: rendering
  status: doing
  classes: [Renderer, VFX, IEffectSink, ParticlePool]
  ---
  Hits and explosions go through IEffectSink so gameplay never touches particles directly.
  ```

  Plain files mean you, Claude, and the viewer can all edit them; the viewer writes the same
  format through `PUT /api/specs/<id>`.
- **Spec store**: one Python module reads, writes, searches, and validates specs (unknown
  class names show as warnings, like lost notes). Server and CLI both use it. Build it on the
  shared Markdown item store (`items.py`, built with tours, 7c).
- Spec files are tiny on purpose, so Claude can find one by search and read only that.

## 7a. Notes (memos), stages, priorities, two-way links (built 2026-10-30, round 2)

User asks (2026-10-30): "specs can be in stages from todo to implemented, a priority for both
work todo and functionality importance"; "Notes in a tab next to specs that reuse the same code
... for comments and such ... todo and done states, but also just an N/A kind of setting"; "Specs
and notes should be bidirectionally linked to nodes and connections".

- **One store for both kinds** (`cards.py`, "cards"): a `Kind` declares only its folder, stage
  list and default stage; everything else (nesting, levels, folding, search, links, priorities,
  AI drafts, checks, JSON) is shared. Kinds: `spec` in `<data>/specs/`, and `memo` in
  `<data>/memos/`, which the viewer calls **Notes**. **Naming**: in code, storage, CLI and API
  "notes" keeps meaning the per-class note fields (`notes.py`, `notes.jsonl`: hint, description,
  rules ...); the Notes tab's items are **memos** everywhere except the UI text.
- **Target keys** (links, wiki links, the coming comments store): `spec:<id>`, `memo:<id>`.
  Comment threads are not built here; memos are standalone items.
- **Stages** replace the status. Spec: `idea → todo → in-progress → review → implemented`, plus
  `dropped`; default `idea`. Memo: `todo → in-progress → done`, plus `na` (shown "N/A"), the
  default for a note that is just information. Older files map on read (`planned` -> todo,
  `doing` -> in-progress, `done` -> implemented; `status` key -> `stage`, `classes` -> `links`); a
  file is rewritten in the new names only when it is next saved. An unset stage is the default
  (and still free for an AI write).
- **Two priorities, one scale**: `priority` (work priority: how soon) and `importance` (how much
  the functionality matters), each `high | medium | low` or unset. Words, not P0-P3: the same
  three words read right for both meanings ("importance: high"), sort without a legend, and three
  steps leave nothing to argue about. Sorting puts high first, unset last, then tree order.
- **Links**: `links` holds classes, connections (`A>B`) and functions (`A#f`) in CLI target syntax,
  resolved with the shared `parse_target` (`items.resolve_target`). Links live only on the card;
  `cards.link_index` computes the reverse index (view id `A` or `A→B` -> linking cards of both
  kinds; a function link counts for its class), the one function behind the view badges, the
  details panel (`/api/linked`) and the CLI (`node` and `zoom` end with `specs:` / `memos:` lines).
  Badges count direct links on shown classes and lines (a line hidden inside a derived line is
  not counted on it).
- **CLI**: `specs|memos <app> [words] [--level N] [--zoom id] [--stage s] [--priority p]
  [--importance p] [--sort tree|priority|importance]` (a filter or sort prints hits with paths);
  `spec|memo <app> <id>`; writes `--add "Title" [--parent] [--links A,B>C,D#f] [--stage]
  [--priority] [--importance] [--level] [--text]`, `--set stage= priority= importance= links+=
  links-= ...` (old `status=`/`classes=` still accepted), `--delete`, `--accept`, `--by`.
  `warnings` lists `spec problem:` / `memo problem:` lines.
- **API** (one router, the kind's folder as the path): `GET /api/specs|memos?app=[&level=N]
  [&zoom=id][&q=words][&stage=s][&priority=p][&importance=p][&sort=...]` -> `{kind, folder, items:
  {id: card}, tree: [{id, finer, folded, folded_links, focus, children}], roots, crumbs: [{id,
  title}], hits: [{kind, id, title, path: "A › B › C", stage, stage_label, priority, importance}] |
  null, max_level, stages, default_stage, stage_labels, priorities, problems: [{id, problem}]}`;
  a card is `{kind, key, id, title, parent, level, level_set, stage, stage_set, priority,
  importance, links, text, order, by, ai, ai_fields, path, children, unknown_links, focus,
  problems}`. `GET .../<id>`; `POST /api/specs|memos` (new, id from the title); `PUT .../<id>`
  (create or update: some of `{title, stage, priority, importance, level, parent, links, text}`
  plus `position`); `DELETE .../<id>` -> `{deleted, moved}`; `POST .../<id>/accept` `{fields?}`.
  `GET /api/linked?at=<class id | A→B>` -> `{at, items: [hit]}`; `GET /api/view?...&badges=1` adds
  `cards` (count) to every node and edge. `GET /api/find?q=` = the class picker. Writes have the
  same guards as every write.
- **Viewer**: one sidebar component (`spec_view.js`) with tabs [Specs | Notes]; per tab its own
  level, zoom, search, filters, sort and selection. Each box has a quick stage picker (a
  chip-sized select) and `work:` / `imp:` chips; filters (stage, work priority, importance) and
  sort (tree, work priority, importance) show the matching items as a list with paths. The
  selected item's card has the priority selects, Edit form (title, level, text), links (+ Link
  selection takes a class, a line or a function row; a class picker; × unlinks), + Sub-item,
  moves, delete confirm and Accept. Selecting an item focuses its links (lines light up). The
  details panel of a class **or a line** lists its specs and notes ("Specs & notes", kind, path,
  stage and priorities; click opens it on its tab). Classes and lines show `§N` badges (Show menu
  "Specs & notes", on by default). URL: `?specs=1` / `?notes=1`, `&spec=<id>` / `&note=<id>`.

## 7b. Sequence diagrams and wiki links (planned)

**Sequence diagrams** show the order of calls for one flow ("player hits an enemy",
"login"), which the class graph can't show.

- **Storage**: one Markdown file per diagram in `.coderstanding/diagrams/<id>.md`, like specs:
  front matter (`id`, `title`, `edges: [A→B, ...]`, `classes: [...]`, `by: you|claude`) and a
  Mermaid `sequenceDiagram` block as the body. Plain text, so you, Claude and the viewer all
  edit the same file, and Claude reads it cheaply.
- **Attached to connections**: a diagram lists the connections (and classes) it covers. In the
  graph, those lines get a small diagram badge; the details panel lists their diagrams and
  shows one inline. A diagram attached to a hidden connection shows on the derived line it was
  folded into, like specs.
- **List view**: a Diagrams list (sidebar section or tab) with search by title, class and
  connection; opening one renders it full size.
- **Drafting**: Claude can draft a diagram from the scanned calls on a path (the call names and
  their order in the source), an AI draft (`by: claude`, `ai: true`) until you accept it. Static order is a guess,
  so drafts say so.
- **CLI**: `coderstanding diagrams <app> [query]` lists them; `coderstanding diagram <app> <id>`
  prints the Mermaid text.
- **Rendering**: Mermaid, vendored into `viewer/vendor/` like Cytoscape (no CDN).

**Wiki links** work in every hint, description, spec body and diagram note:

- `[[Renderer]]` links a class, `[[GameEngine→VFX]]` (or `[[GameEngine>VFX]]`) a connection,
  `[[spec:effects]]` a spec, `[[diagram:hit-flow]]` a diagram. A bare `[[name]]` tries class,
  then spec, then diagram ids, using the same short-name lookup as the CLI.
- In the viewer a link selects/zooms that item or opens that spec/diagram; in slim text it
  stays as written (`[[diagram:hit-flow]]`), so Claude can follow it with one command.
- Broken links (renamed or deleted targets) are listed with the lost notes.
- One shared link parser/resolver serves viewer API, text output and lost-link checks.

## 7c. Tours (backend and viewer built 2026-10-30; viewer notes in 12)

A **tour** is a guided walk through the code: an ordered list of steps, each a saved view
(classes, a connection or a function highlighted) plus presentation text and, optionally,
the real code lines. Back / Next, or **Play**, which reads each step aloud and moves on.
You and the AI both write them. Approved mock-up: `docs/mockups/tours.html` (see below).

**Built (backend, 2026-10-30)**: `items.py` (the shared Markdown item store), `tours.py` (steps,
anchors, stale, authors, audio files), `compute_view(focus=...)`, CLI `tours` / `tour` /
`view --focus` and the API below. Notes on the build:
- File: each step has `key` (stable, 6 hex digits for new steps; reordering keeps recordings);
  `code` also stores `was: from-to` (the lines when `fp` was taken; the API shows it as `was` on
  stale steps); `audio: {rec: {by, fp, dur, ext}}`. Step `by`/`ai` are written only when they
  differ from the tour's. A real tour of this repo, `.coderstanding/tours/scan-pipeline.md`, is
  committed (the rest of `.coderstanding/` stays untracked) and `tests/test_sample_tour.py`
  fails when one of its steps goes lost or changed, so it stays true.
- API: `GET /api/tours?app=[&q=]` -> `{"tours":[{id,title,by,ai,steps,changed,ai_steps}]}`;
  `GET /api/tours/<id>` -> `{id,title,by,ai,steps:[{key,title,text,targets,by,ai,stale,lost,
  lost_targets,code:{at,path,first,from,to,mark,lines,was} | null,audio:{rec:null|{by,dur,stale,url}},
  level,focus,expand}]}` (`code.lines` = today's source lines from..to, `mark` absolute, `first` = the anchor's first line, so `lines` offsets are `from - first`; `focus`
  = targets as view ids and `expand` = their function classes, for `/api/view?focus=A&focus=B&expand=..` (repeated parameters);
  `level` = the step's level or the lowest that shows every target); `GET /api/tours/<id>.md`;
  `GET /api/tours/<id>.txt[?step=N]` = the `coderstanding tour` text (same `format_tour`);
  `PUT /api/tours/<id>` `{title, steps:[{key?, title, text, targets, code:{at, lines:"+a..+b",
  mark:[absolute]}, level?}]}` (steps sent back unchanged stay as they were; new or changed ones
  become the editor's, not AI, and get a new `fp`); `POST .../still-true` and `.../accept`
  (body `{keys?: [...]}`; default: stale steps / every AI step and the tour); `PUT|DELETE|GET
  /api/tours/<id>/audio/<key>` (raw `audio/webm` or `audio/ogg`, max 5 MB, `?dur=`; DELETE has no
  body). Every write answers the tour as GET does.
- `warnings` adds `stale tour steps (code changed since written): <id> 1, 4` and `lost tour
  steps: ...` lines. `--md` prints a plain `path:from-to` (no Forgejo link yet).

- **Storage**: one Markdown file per tour in `.coderstanding/tours/<id>.md`, committed with the
  code, like specs (7) and diagrams (7b). Front matter holds the structure, the body the text:

  ```markdown
  ---
  id: scan-pipeline
  title: How a scan works
  by: you
  steps:
    - targets: [cli#run_scan, cli>scan]
      code: {at: cli#run_scan, lines: +6..+7, mark: [+6, +7], fp: 3f9a2c1b}
    - targets: [scan>language, PythonModule#parse]
      code: {at: PythonModule, lines: +0..+6, fp: 9c41e2aa}
      by: claude
      ai: true
  ---
  ## Where a scan starts
  Everything begins with the scan command. [[cli#run_scan]] checks the folder ...

  ## A language module reads one file
  ...
  ```

  One `## Title` section per step, in step order (the body is the text Play reads; titles
  live there, not twice). Targets use the CLI target syntax (`Name`, `A>B`, `Name#function`,
  `cli.parse_target`). `level` per step is optional (default: the lowest level that shows
  every target).
- **One store for specs, diagrams and tours** (built: `items.py`): specs and diagrams are not built yet, so the
  first of the three builds a shared Markdown item store (front matter + body, read / write /
  search / validate, unknown targets listed like lost notes, `PUT /api/<kind>/<id>`); each
  kind only adds its fields and checks. No second Markdown parser.
- **Code ranges anchor on a target**, never on raw line numbers: `at` is a class or function,
  `lines` are offsets from its first line (clipped to its span: a class's lines, a function up
  to the next member, as `fingerprints.py` already cuts them). The viewer and `tour` print
  today's line numbers, so a step follows edits above it. `at` gone = the step is listed with
  the lost notes. Optional `mark` = the lines to highlight.
- **Changed since written**: `fp` is `fingerprints.digest` of the excerpt (whitespace-free hash,
  the stale-marks code in 10), stored when the step is saved. Different now = the step shows
  "code changed since written" (badge on the step, a dot on its progress dot, a count in the
  tours list) and a **Still true** button that re-stamps, same words and flow as stale notes.
  `warnings` lists stale tour steps under `stale`.
- **Highlight = the view engine's spec focus**: a step is `compute_view(level, focus=targets,
  expand=classes of #function targets)` with the step's connection/function as the
  selection. Today's `spec_classes` widens to `focus` (classes, `A>B`, `A#f`), so hidden
  targets light up their stand-ins or derived lines exactly as specs do. The viewer computes
  nothing itself. Classes one connection from a target show faded, as in zoom (6); a `#function` target unfades the classes it calls or is called by; text lists the faded on a `near:` line.
- **Comments** (7d): threads on a tour, a step or a code line of a step, for the user to tell the
  AI what to change, or co-workers to talk; `tour --comments`, `comment`, a panel in the viewer.
- **Authors**: `by` / `ai` per tour and per step, the notes model (10): an AI step is an
  "AI draft" chip with **Accept** until a human accepts or edits it; the UI says AI, the file
  keeps the real name.
- **Text** supports wiki links (7b): `[[resolver#build_graph]]` selects that target in the
  graph; `[[tour:scan-pipeline]]` links a tour (the shared link parser gains one prefix).
  Specs, diagrams and notes can link tours.
- **Viewer**: a Tours tab (list with search over titles, step text and targets; step count;
  author chip; changed-step count) and the player: graph with the step's focus, step panel
  (title, text, `step 3/7`, targets, all steps), code pane (repo path, today's line numbers,
  marked lines, **Open in editor** = `vscode://file/<abs path>:<line>`, which builds the 12a
  TODO once for the details panel too), Back / Play-Pause / Next, numbered progress dots to
  jump, speed and voice. Keys: Left/Right step, Space play/pause, Home first step.
  `#tour=<id>&step=N` in the URL. **Edit tour** (yellow edit bar like Edit layout): add a step
  from the current selection (class, line or function row), reorder, delete, edit title,
  targets, anchor/lines and text, file preview; saves through the shared store.
- **Play** (steps with an audio file play it instead, see Audio below): the browser's `speechSynthesis` reads `title. text` (links read as their name),
  advances when the utterance ends, Pause cancels it; Next/Back cancel speech and, while
  playing, read the new step. No voice (or a speech error) = timed advance by word count with
  a note. Linux Firefox and Chromium only have voices when speech-dispatcher has one
  (e.g. `espeak-ng`); voices load late in Chromium, so wait for `voiceschanged` first. No TTS
  on the server and nothing vendored.
- **CLI for the AI** (slim text, 11):
  `coderstanding tours <app> [query]` = one line per tour:
  `scan-pipeline How a scan works 7 steps [you] 2 ai, 1 changed`.
  `coderstanding tour <app> <id> [--step N]` = header, then per step `N. Title {targets}`,
  the text, and the code lines with today's numbers (`>` marks highlighted lines,
  `(changed)` after a stale step). `--step N` prints one step; the `view` command for that
  step is its first line, like Copy for AI. Lean on purpose (2026-10-30): only what the tour
  shows, i.e. each step's own excerpt lines, no graph dump, no neighbours, no notes beyond the
  step text. `--marked` prints only the highlighted lines (`path:line` each), `--no-code` only
  titles, targets and text.
  `--md [-o file]` exports the tour as a readable Markdown document (2026-10-30): title and
  author, then per step a `## N. Title` heading, the text (wiki links turned into plain names
  or links), a `path:from-to` line (a Forgejo link when the repo has one) and a fenced code
  block with today's line numbers and the highlighted lines marked; a changed step gets a
  note. For sharing, docs or reading a tour outside the app; same data as the plain text.
  The viewer has the same export: a "Save as Markdown" button on the tour (and in the tours
  list) downloads `<id>.md` from `GET /api/tours/<id>.md`. One export function in the tour
  store serves the CLI and the API; it is a read.
  `coderstanding tour <app> <id> --draft "<title>" <target> ...` = the AI drafts a tour along
  the targets: step order from the given order (or the call order of the scanned calls, as
  diagram drafts do), code anchors from the functions, text from notes and docs; saved with
  `by: claude, ai: true` until accepted. `set`-style edits (`--step N --text ...`) cover small
  fixes; bigger ones edit the file.

Approved mock-up (round 3, 2026-10-30): `docs/mockups/tours.html`, the reference for the build
(layout, controls, word spans, seek, recording, exports); also at
https://claude.ai/artifact/XAwYYYZTBHh9uR8RxMMDcc. Build against it, not from memory.

### Audio (backend built 2026-10-30: files, upload, stale; mock-up round 3)

Every step has a **seek bar** (scrub, elapsed / total) and a **follow-along highlight** in the
step text, without needing any audio file: the seek bar counts words (user, 2026-10-30).

- **What plays**: your recording if the step has one, else the browser voice
  (`speechSynthesis`) reading today's text, else (no voice installed) a silent timer. Same
  rule as notes: a human value beats the default. Source chip per step: "Your recording ·
  <name>" or "Read aloud · seek by words", plus "Changed text: recording out of date".
- **Chunk-synced voice** (browser voice; replaced plain seek-by-words 2026-10-30): engines
  send no word `boundary` events (Firefox and Chromium with speech-dispatcher, measured), and a
  fixed 0.4 s/word clock drifted up to 7 words from the voice. So the spoken text (title + text,
  links read as names) is split into chunks at sentence ends (from 12 characters) and at
  `, ; :` (from 40 characters), a short tail joining the chunk before; one utterance per chunk,
  all queued at once (`chunksOf`, `tours.js`). Each chunk's `start` puts the highlight on its
  first word and `end` on its last; in between it moves by characters (speech time follows
  syllables) at the voice's learned pace: seconds per character at 1x, a moving average of the
  finished chunks per voice name in localStorage (default 0.07). Where an engine does send
  `boundary` events they place it exactly and the clock does not guess. The slider is that
  character timeline: length = characters × pace, shown as `~0:16`. Seeking cancels and speaks
  from the chunk holding the word under the slider, trimmed to start at that word. Queued
  chunks cost a pause per chunk boundary (measured: ~190 ms Chromium + espeak-ng, ~530 ms
  Firefox + its speech-dispatcher voice), hence the minimum lengths. Drift measured on the
  fixture tour (highlight vs the voice's chunk events): Firefox 7 words before, 2 after (1st
  run, pace not yet learned); Chromium 3 before, 1 after. Steps never go stale for audio,
  since the voice always reads the current text. Speed applies to both.
- **Follow-along highlight**: the step text is rendered as word spans that know which spoken
  words they cover (a link like `[[cli#run_scan]]` is one span that speaks as three words);
  the word at the slider's character position is highlighted and earlier words dim. Same
  index for the voice, the timer and recordings (for a recording it follows time by
  characters, so it is approximate).
- **Recording** (optional): Edit tour has Record / Stop / Re-record / Delete recording per
  step. `MediaRecorder` with `audio/webm;codecs=opus` (fallback `audio/ogg;codecs=opus`),
  mono, 32 kbps, echo cancellation and noise suppression on; uploaded unchanged with
  `PUT /api/tours/<id>/audio/<key>` (JSON-only write rule waived for this one audio type, same
  origin checks; max 5 MB, about 20 minutes). WebM from MediaRecorder often has no duration
  header, so the page sends the measured length as `dur`. A recording plays as a real audio
  element with exact seeking. A stale recording (text changed since) still plays (your words,
  your call) and only gets the chip; it is never replaced automatically.
- **Storage**: `.coderstanding/tours/<id>/<key>.rec.webm`, committed with the tour. `key` is a
  short stable id per step in the front matter (not the step number), so reordering keeps the
  file; deleting a step deletes it. Front matter per step: `audio: {rec: {by, fp, dur}}`, `fp`
  = `fingerprints.digest` of the spoken text when recorded. The tour store owns the files
  (read, write, delete, list orphans with the lost notes).
  Opus at 32 kbps is about 4 kB per second; `tour_audio: ignore` keeps recordings out of git.
- **Voices**: on-device only (`localService`, section 18); otherwise whatever the browser has (Linux Firefox/Chromium need speech-dispatcher with a
  voice, e.g. espeak-ng; Chromium loads voices late). A voice picker per browser. No server-side
  TTS: generated audio files (Piper) were dropped in favour of word-count seeking (2026-10-30).
- **Hosted page**: works like the local page (every call goes to the local helper, `apiBase`).
- **Exports**: `--md` and "Save as Markdown" leave audio out; the lean `tour` text prints
  `audio: rec (stale)` on a step's title line only when asked (`--audio`).

## 7d. Comments (built 2026-10-30)

Comments on tours (first), later on specs and classes: the user shows the AI what to change on a
tour it made, or talks to a co-worker about one. Everything stays local; co-workers share through
git, so comments live next to the item and are committed with it. One store for every kind
(`comments.py`); a kind plugs in with `add_kind(prefix, folder, owner)` (the owner says whether a
target exists and which class/function a line comment anchors on). Tours do this in `tours.py`.

- **Targets**: `tour:<id>` (the whole tour) or `tour:<id>#<step key>`; the CLI also takes
  `#<step number>`. A comment can be narrowed to a code line: stored as `anchor: {at, off}`, an
  offset inside the step's code anchor, so it follows edits above it like tour code; gone
  anchor = "Line gone". Or to quoted words of the text (`quote`).
- **A comment**: `id` (8 hex), `target`, `by` (a real name), `ai` (only when true), `created`,
  `changed` (every write), `edited` (text edits), `text` (Markdown, `[[links]]`), `parent`
  (replies join the parent's thread and target), `resolved: {by, at}` on a thread's first
  comment, `deleted` (the line stays, text gone; shown as "Deleted comment" while it has replies).
- **Storage**: `<data>/tours/<id>.comments.jsonl`, one comment per line (sorted keys), sorted by
  thread (its first comment's target, time, id), then time; times in UTC milliseconds. The folder
  gets a `.gitattributes` with `*.comments.jsonl merge=union`, so two people's new lines merge
  without conflicts; a comment changed on both sides then appears twice and reading keeps the
  copy with the newest `changed`. The next write cleans the file up.
- **Lost**: comments whose step (or tour) is gone are listed as lost (a "Lost step" chip in the
  viewer, `lost tour comments: tour:walk#k9 2` in `warnings`), never dropped.
- **Authors**: humans write under `notes.human_author()` = `$CODERSTANDING_USER`, else the repo's
  `git config user.name`, else the OS user (never "you"); the same name for notes and tour
  writes from the viewer. The AI writes as `claude` with `ai: true`; the UI says "AI".
  Anyone can resolve or reopen; only the author edits or deletes.
- **CLI**: `tour <app> <id> --comments [--all]` (header `N open, M resolved`, then
  `[id] step 2 Title path:line author: text`, replies indented); the lean `tour` text adds
  `(2 open comments)` to a step's title line and the header. `comment <app> <target> "<text>"
  [--line N] [--quote ..]`, `comment <app> --reply ID "<text>"`, `--resolve ID`, `--reopen ID`
  (`--by NAME` writes as a human).
- **API**: `GET /api/comments?target=<prefix>` (`tour:walk` = the tour and its steps) ->
  `{me, comments: [{id, target, by, ai, created, edited, text, parent, thread, anchor, path,
  line, line_lost, quote, resolved, deleted, lost}]}`; `POST /api/comments` `{target, text,
  line?, quote?}` or `{parent, text}`; `PATCH /api/comments/<id>` `{text}` (own) or
  `{resolved: bool}`; `DELETE /api/comments/<id>` (own). Writes answer `{me, comment,
  comments}` (the item's comments); same write guards as every write (origin, JSON body);
  someone else's comment = 403. Tour JSON carries `comments` (open threads) on the tour, each
  step and each tours-list row.
- **Viewer** (`viewer/comments.js`, mounted by `tour_view.js` with a box in the step panel and one
  call after each render): the step's threads, "All on this tour (N open)" with Step links, a
  box to comment (Ctrl+Enter sends), Reply, Resolve (folded into a "Resolved by" summary) /
  Reopen, Edit / Delete on your own; a hover "+" in the code gutter starts a line comment, lines
  with open comments get a mark; open counts on the progress dots, the step list, the tour head
  and the tours list.

## 8. Tech stack and external dependencies

A second tab in the viewer (graph | stack & deps), filled by a **stack reader** step in the
scanner. Same data for the CLI: `coderstanding deps <app> [name]`.

What it reads:

| source | gives |
|---|---|
| `project.godot` | Godot version (`config/features`), renderer, C# enabled, autoloads |
| `addons/*/plugin.cfg` | Godot addons with name, version, author |
| `*.csproj`, `Directory.Packages.props` | target framework, NuGet packages + versions, project references |
| `pom.xml` | project name/group/version/packaging, modules, dependencies (`maven` deps `group:artifact`, `${property}` and `dependencyManagement` versions filled in) |
| `build.gradle(.kts)`, `settings.gradle(.kts)` | Kotlin/Android plugin as the stack name, `group:artifact:version` dependencies (the same `maven` deps and `graph.stack["maven"]` list as `pom.xml`, entries with `build: gradle`), included modules |
| `package.json`, `requirements*.txt`, `pyproject.toml` | JS/Python deps for tools or web parts |
| `Dockerfile`, `docker-compose*.yml` | base images, services |
| `.gitmodules` | vendored repos |

Usage: the resolver does not drop outside types. It records them per class and maps
them to a dependency where it can: C# `using` namespaces → NuGet package (by the package's
root namespace), GDScript classes from `addons/` → that addon, Godot API types → "Godot
engine" (counted, not listed one by one). So the tab shows, per dependency, which classes
use it and how much, and a class's detail panel lists its external dependencies.

Addon scripts are not graph nodes by default (they are skipped by the file walk); they show
up here as one dependency each. You can mark an addon as "in the graph" in notes if it is
your own code.

Slim text:

```
$ coderstanding deps mygame
stack: Godot 4.5 (Forward+), GDScript, C# net8.0
addon dialogic 2.0 - used by DialogueBox, NpcTalker
nuget Newtonsoft.Json 13.0.3 - used by SaveSystem
godot-api 214 refs in 38 classes
```

### Outside code in the graph

Packages and addons also show **in the graph** as outside nodes (rounded boxes), one per
dependency, with lines in and out listing their calls (`Viewport → phantom_camera:
follow_target, set_priority`; `phantom_camera → Viewport: signal became_active`). They are
always "visible" at every level, so hidden classes' calls to them fold to the visible owner
like any other line. A toolbar switch hides all outside nodes.

- **Addons are never drawn in full in the core graph.** The core repo does not know an addon's
  insides, only what crosses the boundary. Each addon is one node, so you see what goes in and
  out and how.
- **Your own addons in other repos** (an addon for a game): `coderstanding link mygame
  myaddon` registers it as an addon of the game. Scanning the addon records which game
  classes, signals and hooks it uses; the game's graph then shows one "myaddon" node with
  those lines. Open the addon's own graph to see its insides.
- **The Godot engine API** would connect to almost every class, so it is not a node by
  default; each class's details list the engine types it uses.

## 9. Views and interface bindings

**All types**: every node, interfaces included, `realization` edges drawn.

**Concrete**: interfaces are removed. Abstract classes stay as nodes when they have real code
(any method with a body, or fields); a pure abstract class (only abstract members) is treated
like an interface and removed.
Every edge `X → IFoo` becomes `X → Impl` for each bound implementation, with its text
prefixed "as IFoo" ("as IEffectSink: pushes effect requests"). `realization` edges are not
drawn.

**Bindings** say which classes stand behind an interface:
- Exactly one implementation in the repo → bound automatically.
- More than one, or none → listed in the viewer's "Needs binding" panel. You pick one or
  more implementations; that is saved to notes.
- No DI analysis. If you want a different binding than the scanner's guess, set it.

Per-class **singleton** flag: guessed (autoload, static `Instance`), editable in notes. The
viewer marks singletons with a badge.

## 10. Files

### graph.json (generated)

```json
{
  "version": 2,
  "repo": "mygame",
  "scannedAt": "2026-10-29T12:00:00Z",
  "nodes": [
    {"id": "GameEngine", "name": "GameEngine", "kind": "class", "lang": "gdscript",
     "file": "src/game_engine.gd", "line": 1, "flags": ["autoload", "singleton"],
     "base": "Node", "level": 1, "lines": 240, "doc": "Runs the game loop.",
     "warnings": [{"kind": "god", "degree": 31, "threshold": 24}]}
  ],
  "edges": [
    {"from": "GameEngine", "to": "Viewport", "kind": "composition", "detail": ["field", "call"],
     "at": [{"file": "src/game_engine.gd", "line": 12}], "unsure": false, "auto": ""}
  ]
}
```

### notes.jsonl (yours)

One item per line, sorted by key, so two branches that touch different items merge without
conflicts. Each line is a small JSON object; `by` records who set it (a name: the human's git
user.name via `notes.human_author()` (7d; older lines say `you`), `claude`) and `ai: true` marks an AI draft:

```
{"key":"edge:GameEngine→VFX","hint":"drives rendering, which triggers effects","description":"Each frame GameEngine asks the Viewport to render...","by":"you"}
{"key":"edge:Renderer→IEffectSink","hint":"pushes effect requests","by":"claude","ai":true}
{"key":"node:IEffectSink","bindings":["VFX"],"by":"you"}
{"key":"node:ParticlePool","level":3,"domain":"Rendering","by":"claude","ai":true}
{"key":"node:Player#take_hit","hint":"loses health, then flashes","by":"you"}
{"key":"node:VFX","singleton":true,"description":"Owns every visual effect...","by":"claude","ai":true}
{"key":"node:Net","status":"fragile","tags":["net"],"rules":"never block the main thread","by":"you"}
```

A key can have one line per author (an AI draft level plus your hint); fields merge on
read and a human value wins per field (`you` first among humans). A human write clears that
field from every other author's line, so the newest human edit is the value.

**Stale marks.** A text value (hint, description, review, rules) is only as good as the code it
was written for. When written, the line also stores `"fp":{"hint":"<hash>"}`: a short hash of
that code *now*. `fingerprints.py` computes them at scan time (each source file read once) and
puts them in graph.json (`fingerprints`: note key -> hash), so a stale check needs
no source read. Per key: a class = its own source lines (modules/scripts: the whole file); a
function `node:A#f` = lines from its first line to the next member; a connection `edge:A→B` =
the lines where it is made (its `at` and call sites), which is cheaper than hashing both
endpoints and does not flag a note when unrelated code of the classes changes. All
whitespace is removed before hashing, so reformatting and moved code do not count. If the
stored hash differs from the current one the value is `stale`; a value without `fp` (written
before this, or no readable code) is unknown, never stale. Re-saving or accepting a value
re-stamps it; `set <app> --still-true <targets>` and the viewer's "Still true" button
re-stamp without editing. Known cost: a function's range ends at the next member, so adding
a function right after it counts as a change to it. Views carry `stale` (a list of field
names) on nodes, connections and function rows; `node`/`zoom`/`view` print `(stale)` after the
value, and `warnings` lists them as `stale`.

`layout.json` uses the same style, one line per class: `{"key":"VFX","x":760,"y":130,"pinned":true}`.

Ids are stable across scans (C# full name, GDScript `class_name` or path). When a rescan no
longer has an id that notes mention (rename, delete), the viewer lists those notes under
"Lost notes" so you can reattach or drop them; they are never silently deleted.

## 11. Slim text output

Made for Claude to read cheaply, and short enough for you to read too. One line per
connection, no decoration. Rules:

- Header: `app level view shown/total`.
- `A > B` direct, `A ~> B` derived (with `(via n)` hop count), `*` after a name = singleton,
  `[I]` = interface.
- Your hint after `:`; if none, the edge kinds in brackets. Calls in `{}` (first few;
  `--calls` for all). `[addon]`/`[nuget]` marks outside nodes.
- Zoom output ends with an `out:` line listing faded outside edges.
- Node info (12a): `node` prints `[kind] file:line L2 120 lines, 8 members [wip] #net: hint`
  (`(file)` after the lines when they are the file's), then `warn: god 34, cycle 5`,
  `tests: <files>`, `rules: <first line>` with further lines indented. `zoom` prints
  `rules A:` / `rules B:` for the two ends once, under the header.
- `warnings <app>`: header with counts per kind, then `unused: A, B`, `god (>= 24): Net 80`,
  one `cycle N: A B C` line per cycle, `unsure: A 2`, `unresolved: A 5`.
- `tests <app> <name> [--deep]`: header, then one test file per line, `(via User)` for the
  deep ones.
- `node <app> <name> --functions` (12c): one `A#f > B: m ×2, n` line per function and target,
  `  in: B#g, C` callers under it (`A#f in: ...` when it calls nothing), `A (fields) > B: [kinds]`
  for lines with no function, `no calls: a, b +N`.
- Reading commands (save opening files; targets use the same parser, `Name` or `Name#function`):
  - `outline <app> <target>`: `[kind] file:first-last N lines`, then `functions:` one
    `  name -> ReturnType  file:first-last  — hint` per function, `fields:`, and `inside:` for
    nested types. Function end lines are stored by the readers (`Member.end`, scanner version 7).
  - `find <app> <words...> [--all] [--tests]`: classes and functions whose name, path, notes
    (hint, description, rules, review) or code doc contain every word, best first (name beats
    function name beats hint beats other notes); `Name  kind  file:first-last  — hint`, 20 by default.
  - `impact <app> <target> [--depth N]` (default 3): `1: A, B#f` per distance (function targets
    follow calling functions; a caller with no known function counts as its class), then
    `tests:` to rerun for the target and every dependent found (`testcode.retest_files`).
  - `brief <app> <target...>`: paste into an agent brief. Per target: `Name  file:first-last
    [kind] Llevel [status] #tags: hint`, `rules:`, `warn:`, `fns:` with ranges, up to 5 direct
    links, `ripple:` counts, `tests:` covering it (functions show their own call lines instead).
- `view <app> [--level N] [--concrete] [--zoom A [B]] [--zoom-level N] [--expand X ...]
  [--select X|A>B|X#f] [--tests] [--no-scenes] [--no-external]`: what the viewer shows. First
  line is the command itself (`$ coderstanding view mygame --level 2 --zoom main.gd net --expand
  net --select net`), then the graph/zoom/node text, function lines per expanded class, and a
  `selected X:` block (node info, the line with all calls, or a function's notes and calls).
  `textout.view_text` builds it for both this command and `/api/text`; the viewer's
  **Copy for AI** button copies it (**Copy command only** copies the first line).

```
$ coderstanding graph mygame 1 --concrete
mygame L1 concrete 4/10
GameEngine* ~> VFX (via 3): drives rendering, which triggers effects
GameEngine* ~> Player (via 1): spawns the player when a level loads
Player > VFX: as IEffectSink: requests hit sparks {push_effect}
Player > AudioBus*: plays footsteps {play_sfx}
VFX > AudioBus*: [signal] {effect_played → _on_effect}
GameEngine* ~> phantom_camera [addon] (via 1): steers the camera {follow_target, set_priority}

$ coderstanding zoom mygame GameEngine VFX --concrete
mygame zoom GameEngine>VFX L2 concrete
GameEngine* > Viewport: owns the main viewport {resize, render_frame}
Viewport ~> VFX (via 1): as IEffectSink: [composition, association]
out: GameEngine~>Player, Player>VFX, VFX>AudioBus*

$ coderstanding specs mygame 1
rendering L1 Rendering [doing] {Viewport Renderer VFX IEffectSink ParticlePool} +3
player L1 Player [doing] {Player} +1

$ coderstanding specs mygame turret
turret L3 Player has a turret [idea] {Player} in player
```

A Claude skill (`coderstanding` skill in the repo, installable to `~/.claude/skills/`) tells
Claude to run `graph` before exploring an unfamiliar registered repo, then `zoom`/`node`
instead of reading files.

### Copy for AI (planned, 2026-10-29)

A "Copy for AI" button copies what's on screen as slim text: first line is the equivalent command
(`$ coderstanding view mygame --level 2 --zoom main.gd net --expand net --select net`), then the
graph/zoom lines (with fan-out lines for expanded classes), then a short block for the
selected item. "Copy command only" copies just that first line, which Claude can re-run.
`coderstanding view ...` prints the same text; one shared function serves both.

## 12. Viewer (first version)

- Slider: levels 1..max, with node counts per level.
- View toggle: All types / Concrete.
- Edge style rules, one meaning each:
  - **Outline (hollow line)** = the connection runs through hidden classes. Outside-the-zoom
    classes are plain grey outlines. Hidden-ness is never shown with dashes.
  - **Line pattern and arrowhead = relationship kind**, following UML where it has a
    convention: `composition` solid with a filled diamond at the owner, `association` solid
    with an open arrow, `dependency` dashed, `generalization` solid with a hollow triangle,
    `realization` long-dashed with a hollow triangle, `signal` dotted.
  - Colour: direct, derived, outside (grey), selected spec (highlight).
- Directed edges with arrows; label text
  on the edge when it has a note.
- **Details panel** for whatever you hover over or click. Edge: hint, kinds, every call with
  count and code location, the via path with calls per hop, long description. Node: file,
  kind, flags, level (editable), singleton toggle, bindings (interfaces), lines in and out,
  specs, engine types used, long description. Hint and description are editable here.
- Domain cards (section 6b) and an outside-code switch.
- **Layout: View and Edit modes.**
  - Every class has one spot, the same at every level, so the graph doesn't jump when the
    slider moves. Domain cards size themselves around their classes.
  - **View mode** (default): you can drag classes to look around, but nothing is saved; they
    snap back when the level, view, zoom or page changes (opening or closing a class keeps them).
  - **Edit layout mode**: drags are saved. The mode is unmistakable: yellow construction stripes
    across the graph background and a yellow "Editing layout" bar. Undo/redo while editing.
  - **Pin**: a pinned class can't be dragged in View mode and Auto layout never moves it. In
    Edit mode you can still drag it (the pin moves with it).
  - **Auto layout** button places every unpinned class (`viewer/layout.js`): one band per
    level, level 1 at the top, each band layered left to right (callers left, callees right,
    fewer crossings); outside code in a grid at the right of the top band, scenes in their
    script's band. Spots come from the fullest view, so low levels stay compact. In View
    mode it's a preview only and snaps back like any View move; in Edit mode it applies and
    is saved (and can be undone).
  - New classes from a rescan get an automatic spot next to their owner; nothing else moves.
  - Saved spots and pins live in `.coderstanding/layout.json`, one line per class, sorted by
    id, so parallel branches merge cleanly.
- Double-click a line or class to zoom (section 6; the details panel also has a Zoom button); breadcrumb to step back.
- Tabs: Graph | Stack & deps (section 8) | Tours (7c, viewer built 2026-10-30).

### Tours tab (built 2026-10-30)

Built against the approved mock-up (`docs/mockups/tours.html`) and the tours JSON API (7c).
- **Graph**: the graph tab's own canvas (Cytoscape + fan-out layers) moves into the Tours tab
  while it is open; each step asks `/api/view` with the step's `level`, its focus as repeated
  `focus=` parameters (one per view id, like `expand`; never comma-joined) and its `expand`, draws the server's `focus` ids (classes, `from→to` lines) in the highlight
  colour, framed (never below the readable zoom), and selects the first focused function.
  Leaving puts the canvas, level, zoom path and open classes back.
- **Player logic** in `viewer/tours.js` (pure, `node --test`); the tab in `viewer/tour_view.js`.
  Every write (accept, still true, PUT, audio) goes through `CoderApps.request`, so the hosted
  copy writes through the helper and hides + New, Edit tour, Accept, Still true and Record
  unless the helper answers. Save as Markdown is a plain download link to `GET /api/tours/<id>.md`.
  "Step as AI text" shows `GET /api/tours/<id>.txt?step=N` (without `step`: the whole tour),
  exactly the `coderstanding tour <app> <id> --step N` text (one `textout.format_tour` for both),
  with Copy for AI. Writes send `{"keys": [key]}` and show the tour each write answers with.
  The list searches with `GET /api/tours?q=`. The editor's marked lines are today's line
  numbers; its `lines` are offsets inside the anchor, from `code.first` (the anchor's first line
  today, in each step's `code`). A lost anchor has no `first`: its lines field stays empty and
  Save asks for it.
- **Speech findings** (2026-10-30, Fedora, speech-dispatcher + espeak-ng): Chromium lists no
  voices unless started with `--enable-speech-dispatcher`; with it, ~15000 voices arrive about
  100 ms late (`voiceschanged`), the first (Afrikaans) is flagged `default`, `speak()` without
  a user gesture fails with `not-allowed`, and no `boundary` events fire. Firefox lists the
  speech-dispatcher voices, speaks without a gesture (also in a sandboxed cross-origin iframe)
  and fires no `boundary` events either. So: the voice picker lists the page language's voices
  and picks one for that language (never the bare default); the slider always runs on the
  clock (boundary events only correct it) and waits at 98% for the voice's `end`; a voice that
  sends no `start` within 2.5 s, or errors, leaves the step on the clock with a note; voices
  that load late start speaking at the current word.
- Collapsible spec sidebar (section 7): nested boxes, own detail slider, spec zoom, search, status
  chips, highlight attached classes, edit and add specs.
- Search box that also forces a matching hidden node visible.
- Panels: Needs binding, Lost notes.
- **Type toggles** in the toolbar: Scenes, Outside code, Domains (more types can join later).
  A type switched off folds into its owners like a hidden level, so no connection is lost.
- Edits save to `notes.jsonl` through the server right away.

Later: filter by edge kind, drafting hints and descriptions with Claude
from the code locations.

### Auto layout methods (requested 2026-10-29)

"Auto layout" is a dropdown: **Layered** (default, decided 2026-10-29: bands per level, the
app's start in the first column, tools in a column at the far left), **Organic** (force-directed,
spread out, seeded so it's repeatable), **Hub** (radial around the selected class) and **Grid**
(big graphs; automatic fallback above ~1500 nodes). In every method outside code sits right
beside its first (lowest-level) user. A choice remembered per browser wins over the default.
Pinned and saved spots stay fixed in every method; View previews, Edit saves. Hub centres on the selected class (a
selected function counts as its class, a selected line as its "from" class), else the app's
start; the centre class stays where it is drawn, and the button's tooltip names it ("Hub around
net.gd"). After Auto layout the view re-fits (readable zoom).

First-run spots (2026-10-29 QA): Layered places every class at once (one band per level, so
level 1 is laid out on its own); Organic builds up level by level (level 1 alone, then each level
around the earlier ones), so the hubs at level 1 are not a knot inherited from the full view.
Repulsion and the minimum gap scale with box size. The first fit never zooms below a readable
0.82 (box names ~11px): if the whole graph doesn't fit, it fits the classes without outside code,
else the start and hubs, else centres on the app's start; the user pans for the rest. Class-line
bends never take a detour longer than 1.4x the straight line (+60): such lines stay straight.

### Start node (requested 2026-10-29)

One synthetic `start` node (not a class) drawn as a plain arrow at the left edge, visible at
every level, with numbered lines into the real entry points in start order: Godot autoloads
in `project.godot` order, then the main scene (which runs its root script); C#/C++
`Main()`/`main()`; Python/Node app entries. Tool scripts (`script-main`) get no start line.
Built by the view engine (it is view data, not scan data); text output prints
`▶ start > background.gd ①, net.gd ②, ..., main.tscn ⑤`.

## 12a. Extra node info (decided 2026-10-29)

- **Tests**: test code is marked (flag `test`: `tests/`/`test/` folders, `test_*.py`,
  `*_test.*`, `*.test.ts`/`*.spec.ts`, GUT `test_*.gd`, C# test projects) and **hidden by
  default** (a "Tests" type toggle shows them, like Scenes/Outside code; hidden tests are
  removed, not folded, so they never distort the graph). Every class lists **"Tested by"**: the
  test files with lines to it. `coderstanding tests <app> <name>` prints which test files to
  run after changing it (direct, `--deep` adds tests of its direct users).
- **Status + tags**: a status (`stable`, `wip`, `fragile`, `deprecated`) and free tags, set in
  notes, shown as chips, filterable on the graph.
- **Rules / gotchas**: a notes field for "never do X here" knowledge. Printed by `node`,
  `zoom` (for both ends) and shown in the details panel; meant to be pasted into agent briefs.
- **Warnings** (computed at scan): unused (nothing points to it, not an entry/test), god class
  (connections far above the repo's norm), part of a dependency cycle, unsure or unresolved
  references. Shown as a warning chip and listed in `node` output and a `warnings` command.
- **Size**: line count and member count on every node, shown subtly.
- **Built (2026-10-29)**:
  - Test rule in one place (`testcode.py`): folders `test`, `tests`, `__tests__`, `*.Tests`,
    `*.Test`; files `test_*.*`, `*_test.*`, `*.test.*`, `*.spec.*`, `*Tests.cs`; C# projects
    whose csproj references xunit/NUnit/MSTest/Microsoft.NET.Test.Sdk. Tests are never
    level-1 roots in the level guess, are left out of `curate`, and lose short-name ties.
    "Tested by" = test files with a line to the node or to a node it is nested in (a test
    of a module covers its classes).
  - Size: classes with a tree-sitter span (C#, Python, JS/TS classes, GDScript inner classes)
    use their own lines; files (modules, scripts, scenes) use the file's lines (`span` false,
    printed `(file)`). Members = the node's member list.
  - Warnings (`checks.py`, at scan, stored in graph.json v2): unused = no incoming line from
    non-test code, skipping entry/autoload/test/outside nodes; god = distinct neighbours
    >= max(20, 6 x repo median); cycle = strongly connected set > 1 over non-inheritance
    lines; degree and cycles ignore tests, outside code and type/nested-type pairs; unsure =
    outgoing unsure lines; unresolved = outside refs that are no package, engine API or
    Python standard library. Static reading misses dynamic loads, so `unused` is a hint.
  - Note fields have one set of rules each (who it is for, what goes in, what does not, a good and
    a bad example), built in as `docs/field_rules.json`; a project may override any part, and add
    house rules for AI notes, in `<data>/fields.md` (beside tours; shared through git; a section per
    field, `key: value` lines, edited by `coderstanding fields`, the details panel's "Edit field
    rules" or by hand). `field_rules.effective` is the one resolver: the viewer's "?" tips and form
    (`GET/PUT /api/fields`), `guide --app`, `curate` and the AI_GUIDE section all read it. `hint` and
    `description` are for a non-technical newcomer: what it is FOR, never how the code works. `set`
    (AI writes only) prints a reminder, never refuses, for a hint over `max` characters or
    code-like hint/description text (identifiers with `()`, `snake_case`, `camelCase`, `::`, generics,
    type names, code fences). The AI_GUIDE field section is checked against the built-in rules by a test. Meaning in short: `hint` = one line, what it is or does; `description` =
    what the class is and what its functions do (a connection: what it represents and why it
    exists), never problems; `review` = code-review findings (smells, risks, suggested splits,
    with concrete targets; classes, connections and functions; `node` prints one short
    `review:` line, `zoom` one under the connection; the details panel has a separate Review
    section, hidden while empty except in Edit mode); `rules` = "never do X here".
  - Status/tags/rules are notes fields (`by: you|claude`), edited in the details panel; the
    toolbar Highlight menu dims classes without the chosen status or tag.
- **Stale notes (built 2026-10-30)**: see "Stale marks" under notes.jsonl (section 10). The
  details panel shows a small "code changed since written" badge and a "Still true" button on
  the field.
- **Later (low priority)**: "Open in editor" (VS Code link to file:line; Tours (7c) need it, so it
  lands with them and the details panel shares it) and a Forgejo file
  link. Git history per file was offered and not picked for now.

### Architecture rules (built 2026-10-30)

User-declared dependency constraints, checked on every scan and shown as warnings. Extends
`checks.py` (no new checker) and the existing per-folder `rules`.

- **Rule shape** (`settings.json` key `layers`, beside `rules`): `{from, to, mode, kind?, why?}`.
  `from` / `to` are a folder glob (the `checks.glob_regex` matcher, matched on `node.file`),
  `domain:<name>` (6b) or `tag:<tag>` (12a). `mode`: `forbid` ("src/ui must not depend on
  src/net") or `only` ("only `src/net/**` may use `tag:network`": every user of `to` outside
  `from` is an offence). Optional `kind` limits it to one edge kind (`call`, `inheritance`,
  `signal`, `dependency`, ...); `why` is a free line shown with the warning.
- **Check**: one pass over the edges in `check_graph`. Tests and outside code on the `from` side
  are skipped unless a rule names them; derived (collapsed) edges are never used, only real
  ones. An edge hitting a rule gives its `from` node a `layer` warning that carries the
  offending edge (target id, kind, line), so the count is the number of bad edges.
- **Warning kind `layer`**: added to `WARNING_HELP`, so it is ignorable like the others (note
  field `ignore_warnings`, or an existing rule with `ignore: ["layer"]`; reason text as today).
  `warning_text` names the edge: "layer: `NetClient` (src/net) via `call` at ui/hud.gd:42".
- **CLI**: `warnings` lists `layer` with the edge; `rules` lists both kinds, numbered
  together, and `rules add-layer FROM TO --forbid|--only [--kind K] [--why TEXT]` /
  `rules remove N` manage them (same `apps.py` path as `add_rule`, validated by a sibling of
  `parse_rule`; unknown domain/tag/glob matching nothing is an error).
- **Viewer**: the warning chip on the `from` class, the offending line drawn in the warning
  colour with a mark, and the connection's details panel says which rule it breaks and its
  `why`.
- **Built notes**: the pass is `checks.layer_warnings`; `check_graph(graph, layers, notes)` runs it,
  but settings and notes are not part of a scan, so loaders (CLI `open_app`, server `notes`) call
  `apply_layers` again (`App.checked`), which makes rule changes live without a rescan. Edge kinds
  are the model's plus `call` (line with calls) and `inheritance`. Tests count on the `from` side
  only when the `from` glob contains "test". Each line shows `edge.layer` in the view JSON; the
  viewer draws it in the warning colour with a warning sign in its label and lists the rule and
  `why` under "Breaks a rule" in the connection panel (the class panel shows `why` beside the
  warning). Expanded-class function wires are not coloured yet.
- **From docs**: rules written in a project's `docs/AGENTS.md` ("UI never touches the
  network layer") can be turned into `layers` entries by the AI (skill: read the doc, call
  `rules add-layer`, show the user the list). Nothing parses prose in the tool itself.

### Godot loose ends (built 2026-10-30)

New warning kinds in `checks.py` for Godot apps, from reader data that exists or is cheap:

| Kind | Meaning | Reader data |
|---|---|---|
| `signal-unconnected` | signal declared (and emitted) but nothing connects it | declared signals: have (member kind `signal`). Connections: have (`signal` edges from `x.sig.connect(...)` and scene `[connection]`). Emits: **missing**, needs `sig.emit()` / `emit_signal("sig")` per member |
| `signal-unemitted` | signal connected but never emitted | same three; ignore signals of engine types (`pressed`, `body_entered`) since only the project's own signals are declared |
| `bad-handler` | scene connection or `connect()` names a method that doesn't exist | connection `method` and the target script's method members: have for scenes (`handler_name` / `[connection] method`); for `connect(` with a `Callable`/bare name, have the name; method lookup must follow `extends` |
| `autoload-unused` | autoload nothing uses | autoload flag and incoming edges: have. **Missing**: autoload calls and signal hookups are read as plain `dependency` (TODO line), so "uses" is unreliable until they get call/signal edges |

- **Reader work** (GDScript module only, no core change): record emits as member-level refs
  (new detail `emit`, same shape as the 12c `from_member` refs, so no new edge kind in the
  normal view); read autoload-name calls (`Events.hit.emit()`, `Game.start()`) as `call` /
  `signal` instead of `dependency`; keep string-form `emit_signal("x")` and `connect("x", ...)`
  (Godot 3 style) when the name is a literal.
- **Static limits**: dynamic names, `Callable(self, name)` with a variable, signals used only by
  an addon or from C#, and `get_node(...).connect` with unknown type are not seen, so these
  are hints like `unused`: ignorable by note or folder rule, and a signal with any unresolved
  hookup in its script counts as connected.
- Warnings go on the declaring class (signal kinds, autoload) or the connecting class
  (`bad-handler`) with the signal/method name in the text. They are Godot-only: other
  language modules never produce them.
- **Built (2026-10-30)**:
  - Readers: an emit is a `dependency` / `association` ref with detail `emit` and the signal
    name as the call (`hit.emit()`, `self.hit.emit()`, `emit_signal("hit")`, `done.emit` passed
    as a Callable, `Events.hit.emit()`, C# `EmitSignal(SignalName.Hit | nameof(Hit) | "Hit")`);
    a non-literal name is `*`. Signal hookups on something untypable (`a.b.sig.connect(h)`,
    `get_node(..).sig.emit()`) go to the engine type `Object`, so the name still counts; the
    check treats names seen on non-class targets as "anywhere". String `connect("sig", ...)`
    (Godot 3 or 4) and C# `Connect` / `Sig += h` read too. Handlers: `Other.m` stays dotted,
    `lambda` and `<callable>` (a variable) are opaque. `func _init()` bodies were skipped before;
    they are read now.
  - Checks (`checks.py`, `SignalUse`): per class, emitted / connected names (a base class and
    its subclasses share them; a plain `await other.sig` access counts as connecting).
    `signal-unconnected` = emitted, not connected; `signal-unemitted` = connected, not emitted
    (declared-only signals get nothing); `bad-handler` = a GDScript `connect` / scene
    `[connection]` handler named `_on..` / `on_..` / `_handle..` that no member of the script or
    its base scripts has (engine functions are unknown, so other names are not checked);
    `autoload-unused` = autoload nothing outside tests points at. Warning dict: `names` + `count`.

## 12c. Expand a class into its functions (mock-up approved 2026-10-29, built 2026-10-29)

- **Scanner**: every reference records the function it sits in (`from_member`: method,
  function, or `<init>`/`<field>` for field initializers and top-level code), next to the
  member it calls on the other side (already kept as `calls`). One field on the shared `Ref`
  and `Call` types; each reader fills it from its enclosing function. Old caches are dropped by
  bumping the scanner version.
- Approved mock-up: https://claude.ai/artifact/L8yqGshbKkRxPrmZvF4XBx (look and behaviour to match).
- **Viewer**: an expand button on a class (and on modules/scripts) turns it into a box holding
  its functions (Cytoscape compound node; fields and signals in a smaller list). Lines that
  touched the class **fan out**: each starts at the function that makes the call, and ends at
  the called function when the other class is expanded too, else at the class. Lines with no
  function (field types, inheritance) stay on the class box. Collapse puts it back. Expanded
  state is per view, not saved; an Edit-mode option can pin a class as always expanded.
- **Levels and folding are unchanged**: expansion is applied after `compute_view`, on visible
  classes only; derived lines fan out at their visible ends using the per-hop calls.
- **Details panel** for a function: its calls out and in (with counts and code locations) and
  the same Hint/Description/Rules notes, keyed `node:<class>#<function>`.
- **Text**: `coderstanding node <app> <name> --functions` prints one line per function with its
  outgoing calls (`net.gd#join_room > media: upload, clean_file_name`) and incoming callers.
  That answers "who calls X?" for Claude without opening files.
- **Built (2026-10-29)**:
  - Engine: `compute_view(..., expand=[ids])` (`*` = every shown class; `fanout.py`) returns
    `expanded` (`{id: {functions: [{name, line, hint, ..}], fields: [{name, kind, type}]}}`:
    method/function members, then callers only seen in calls such as `<init>`; fields,
    properties, consts, events and signals) and `fanout` links `{key, edge, from, from_fn, to,
    to_fn, kinds, direct, internal, faded, as, members: [{name, count}], count, at}`; edges
    touching an open class get `fanned: true` (the viewer draws the links instead).
  - Rules: a call starts at `from#from_member` when the source is open (`<field>`/None stay on
    the class); it ends at `to#member` when the target is open and has that function (a signal
    call `sig → handler` ends at the handler), else at the class with the names on the line.
    Field types, bases and call-less lines are a class-to-class link. Composition calls are
    drawn as association (the diamond stays on the field's line). Derived lines: from each
    calling function of the first hop; to a function only when the last hop calls one name;
    from the class when the source end has no function. Internal links: self-edges of an open
    class (the resolver drops self-references today, so real scans have none yet).
  - Viewer (`viewer/fanout.js`): ▸/▾ on every class box (not outside code), Expand all (capped
    at 30) / Collapse all, open boxes grow right and down from their top-left corner (nothing
    else moves; an open box is drawn over boxes it covers), rows listed in member order (classes with
    more than 40 functions show only the ones with lines, `+N functions without lines here`;
    fields capped at 12), lines in an SVG layer under the boxes with the same UML styles, labels
    over them (click selects the class line, double-click zooms). Hover or click a function:
    its lines light up, others dim, details show Calls / Called by with counts and locations,
    plus Hint/Description/Rules notes. Expanded state is per page (`?expand=` for links).
  - Double-click a line or class zooms (single click selects); only ▸/▾ toggles expanding.
- **Polish (2026-10-29)**: box faces drawn like the mock-up (header 46, rows 24, fields 18,
  tinted header, bold mono name, "N functions · M fields", drawn accent triangle toggle with
  aria-expanded); open boxes list every function (over 20: the list scrolls inside the box with a
  filter box, off-screen rows' lines attach to the list edge with "↑/↓ N more lines"). Fan-out lines: one per function pair, as the mock
  (cubic, dx = max(50, half the gap)); 4+ lines between one class pair share a trunk that splits
  near its ends. Calm views label every line like the mock; busy ones (over 16 lines or 10
  labels) show only ×N counts until a function or line is hovered or selected. Labels are placed
  greedily along their line, off boxes and other labels (`layout.js`), also for class lines;
  class lines that would cross other boxes get bend points. Start badges name the mechanism;
  warnings are a corner mark and can be ignored per kind (notes field `ignore_warnings`) or by app rules (`settings.json` `rules`: folder glob, optional `lang`/`kind`, kinds or `all`; `coderstanding rules`), the ignored list showing the reason ("note" / "rule tools/** unresolved"); rule-ignored cycle members leave the cycle line, which goes below 2 members; a
  `hub` class shows "Hub · N connections".
- **Auto layout methods (2026-10-29)**: a split button picks Organic (default), Layered, Hub or
  Grid, remembered per browser. Organic is a deterministic force layout in `layout.js` (started
  from the layered spots; springs scaled by label width; outside code on short stiff springs;
  `script-main` tools pulled to a left column; fixed spots stay; overlaps pushed apart). Hub =
  rings around the selected class or the app's start. Organic and Hub fall back to Grid above
  1500 classes. Own code instead of Cytoscape `cose` (cannot pin fixed spots) or `fcose` (three
  vendored libraries) so it stays pure and tested.

## 12b. Hosting on your own server

The hosted page on your server (here "the lab") is a thin front door: a container
`coderstanding` on your reverse proxy's network, no host ports, behind your own login proxy
(e.g. oauth2-proxy with your git forge's OIDC and an allow-list of emails) →
`coderstanding:8517`. The maintainer's own setup (host names, proxy, deploy commands) is kept out
of the public copy, in `docs/private/LAB.md`. It serves the viewer only; all data comes from
`coderstanding serve` on the visitor's own PC (v3).

### v3: the hosted page is a shell for the local helper (2026-10-30, current)

**No maps live on the lab.** Decided 2026-10-30 by the user ("all the graphs are local") after a
huge app (28k classes, a 320 MB graph.json) synced to the lab made the 512 MB read-only
hub run out of memory and crash-loop. The v1 read-only mirror and the v2 auto-sync (below, for
the record) were dropped that day: `coderstanding sync` and its script, `sync.py` /
`AutoSync`, `serve --read-only` / `--no-sync` / `--host`, `/api/sync`, the hosted copy's
`tour_code.json` snapshot and the any-scanner graph load are gone. Old data on the lab
was left in place for the user to clear.

- **Hosted page** = the viewer as static files plus `hosted.json` (`{"helper":
  "http://127.0.0.1:8517"}`, from `deploy/hosted.json`), served by Python's built-in
  `http.server` in the same `coderstanding` container (read-only, 64 MB, no Coderstanding
  code), still behind the login gate. The repo's own
  `viewer/hosted.json` says `{"helper": null}`: a local `serve` page talks to its own server.
- **Helper first**: the page reads `hosted.json`; when it names a helper, the page probes
  `<helper>/api/apps` (1.5 s) and then sends **every** API call there: app list, views, node,
  stack, tours, layout, notes, scans and writes. `CoderApps.apiBase(hosted, helper)` is the one
  decider and `CoderApps.url()` / `CoderApps.request()` use it for every request. The page shows
  the PC's own registry, and the toolbar label reads "Running on this PC". Big apps cost only
  the PC's memory.
- **No helper** (phone, PC off, helper stopped): no data and no read-only copy, just an empty
  state: "Open this page on your PC with Coderstanding running", how to start the helper
  (`systemctl --user start coderstanding-helper` or the tray icon), to allow the browser's local
  network prompt, and how to install it once (`scripts/install-helper.sh`).
- **Deploy** = the lab's deploy script: rsync `viewer/` + `deploy/hosted.json` to `web/`,
  `docker compose up`, one health check of `/hosted.json`. Quick, no data step.
- **Security** (local server, unchanged; offline guarantee in 18): binds 127.0.0.1 only. CORS for exactly one origin,
  read from `~/.config/coderstanding/hosted.json` (`{"origin": "https://code.your-server.example"}`;
  `--allow-origin` / `CODERSTANDING_ALLOW_ORIGIN` override it), now
  on reads too since the page reads everything from the helper; preflights answer
  `Access-Control-Allow-Private-Network: true`. Writes (POST/PUT/DELETE) are refused with 403
  when an Origin header is present and is neither the server's own `http://127.0.0.1|localhost:<port>`
  nor the allowed one, and with 415 unless the body is `application/json` (tour recordings: raw
  audio). Requests whose Host is not 127.0.0.1/localhost are refused (DNS rebinding).
- **Run at login**: the helper runs as the systemd user unit `coderstanding-helper.service`;
  `coderstanding-tray.service` shows a tray icon polling `/api/apps` (green up, amber scanning,
  grey down; tooltip: app count, scans, scan errors), installed by `coderstanding helper install` (`scripts/install-helper.sh`), which also starts the helper and
  verifies it (polls `/api/apps` through the no-proxy opener for ~8 s; on failure prints the last 10 log
  lines and the log path; `helper start` verifies the same way, `--no-start` skips). `serve` starts with
  zero projects (empty state, `/api/apps` lists none); `serve --service` startup errors go to the log and
  to a console's stderr, exit code 1.

### App manager (built 2026-10-30)

Toolbar "Projects…" opens a modal listing every project the helper knows: name, repo and data folders,
last scan, scanner version (old ones flagged "refresh"), classes/connections, graph.json size,
notes and tours counts, scan errors. Per app: Open, Refresh, Rename (registry name only; files
stay put), Remove. Remove asks inside the modal: "Remove from the list (keep its files)" or
"Remove and delete its scan data" (graph.json, cache/, layout.json, an old tour_code.json); an
extra box, listing them by name, also deletes notes.jsonl, specs/, tours/, settings.json in the
data folder. Nothing outside the data folder is ever deleted, and a data folder that is (or holds)
the repo, or sits in the repo anywhere but `<repo>/.coderstanding`, is refused. "Add project…"
sits at the bottom. API (helper only, the usual write guards): `GET /api/apps?details=1`,
`DELETE /api/apps/<name>?data=0|1&notes=0|1`, `PATCH /api/apps/<name>` `{"name"}`; CLI
`coderstanding remove <app> [--delete-data [--delete-notes]]`, `coderstanding rename <old> <new>`;
both use `apps.remove_app` / `rename_app`.

### v1: read-only mirror (built 2026-10-29, dropped 2026-10-30)

A read-only copy of the desktop's maps: `serve --read-only` in the container read
`/data/<app>/` synced by `coderstanding sync` (rsync of graph, notes, layout, specs, tours and a
`tour_code.json` excerpt snapshot, plus a merged `apps.json`), refused every write and labelled
itself "Read-only copy · synced …". Dropped with v2 (see v3).

### v2: local helper and auto-sync (built 2026-10-29, dropped 2026-10-30)

The hosted page sent writes to the local helper and read from the hub; `AutoSync` pushed each
changed app ~2 s after a write and the page waited for the push before reloading from the hub.
Replaced by v3: reads and writes all go to the helper, nothing is pushed.

### Lab setup (still current)

- **Public behind a login** (2026-10-29; was LAN-only earlier that day): the reverse proxy
  sends the host to the login proxy, which passes only allowed emails; own TLS certificate,
  Force SSL + HSTS. The maintainer's setup: `docs/private/LAB.md`.
- **Scanning never happens on the lab**; it happens on the PC that opens the page (Refresh and
  Add project go to the helper). A browser-only scanner (Pyodide) could come later.

## 13. Testing

- Scanner: pytest with small fixture repos under `tests/fixtures/`
  (`csharp_basic`, `godot_gdscript`, `godot_csharp`, `python_basic`, `ts_basic`), asserting
  nodes and edges. Real-repo smoke runs on Coderstanding, the test game and its addon.
- View engine (collapse, concrete, zoom) and text format: pytest on small graph fixtures.
  Most bugs will live here, so it gets the most tests.
- Viewer UI: manual for now.

## 14. Milestones

1. **Model + scanners**: graph model and JSON writer, resolver, file walk, level guesser,
   `scan` CLI first; then in parallel: C# module; GDScript + Godot project/scene + stack
   reader; Python + JS/TS modules. Then scan Coderstanding, the test game and its addon.
   After the readers: per-file cache + parallel parsing (4.0), timed on the test game.
2. **View engine + text CLI**: collapse, both views, zoom, slim text, `add`/`graph`/`zoom`/
   `node`. Usable by Claude before any UI exists.
3. **Viewer**: `serve`, drawing, slider, view toggle, zoom with breadcrumbs.
   Includes `curate`/`set`, and the first curation pass on the test game to check the levels.
4. **Notes and specs**: hint editing, level overrides, bindings, singletons, lost notes;
   spec store + `specs`/`spec` CLI + sidebar; `deps` CLI + stack tab; Claude skill.
5. **Hosting**: hub mode, apps page, Forgejo scan, upload, deploy to a server (12b).
6. **Expand classes into functions** (12c): `from_member` in every reader, compound nodes with
   fan-out lines, function notes, `node --functions`.
7. **Sequence diagrams and wiki links** (7b): diagram store + CLI, viewer list and badges,
   Mermaid rendering, link parser, Claude-drafted diagrams.
8. **Tours** (7c): shared Markdown store (or reuse it if specs/diagrams built it first), `focus`
   in `compute_view`, anchored code ranges + fingerprints, `tours`/`tour` CLI and AI drafts,
   Tours tab with player, TTS and Edit tour, Open in editor. After 7 (wiki links).
   Then audio: word-count seek bar and follow-along highlight, optional recordings
   (MediaRecorder upload).
9. **Architecture rules** (12a, built): `layers` settings, `layer` warning, `rules` CLI extension, offending lines in the viewer.
10. **Godot loose ends** (12a): built 2026-10-30: GDScript / C# emit and hookup reading, then the signal / handler / autoload warnings.
11. **Tune on real repos**: run on Coderstanding, the test game and its addon, fix resolution gaps, measure token savings (same tasks with and without the graph).

## 15. Out of scope (for now)

- Dependency-injection containers and runtime analysis.
- Languages beyond C#, GDScript, Python and JS/TS (the language-module split keeps the door
  open).
- Method-level graphs (call graphs inside a class).

## 16. Decisions from planning (2026-10-29)

- Specs are this tool's own; `docs/COMPONENTS.md` stays project-specific and is not imported.
- Notes and layout are one item per line, sorted (merge-friendly).
- Selecting a spec shows its classes plus one faded layer out, like zoom.
- Abstract classes with real code stay as nodes in Concrete view.
- Scenes are nodes; type toggles switch scenes / outside code / domains on and off.
- Claude sets levels, domains and drafts hints through `curate`/`set`; your values win.
- Making it part of Claude's normal flow comes later; what an agent needs is collected in
  `docs/AI_GUIDE.md`.

## 17. Open questions

- Otherwise none blocking. Revisit after the first scan of the test game.

## 18. Offline guarantee (built 2026-10-30)

The user runs it at work on company code: it must make **zero** outside calls (user,
2026-10-30). Rules, each enforced by a test (`tests/test_offline.py`, e2e):

- **Runtime guard** (`offline.py`): `install()` adds a `sys.addaudithook` hook (cannot be
  removed) that raises `OfflineError` naming the destination for every `socket.connect` /
  `connect_ex` / `sendto` / `sendmsg` / `bind` and every name lookup (`getaddrinfo`,
  `gethostbyname[_ex]`, `gethostbyaddr`, `getnameinfo`) that is not loopback. Allowlist:
  127.0.0.0/8, ::1 (also IPv4-mapped), the names `localhost` / `localhost.localdomain` /
  `ip6-localhost` / `ip6-loopback`, AF_UNIX paths, and lookups for no host. The hook sits in the
  C socket module, so `_socket` and Windows (`localhost` = ::1) are covered; nothing in it is
  POSIX-only. Installed by `cli.main` (CLI, `serve`, the `rescan` child), the scan process pool's
  initializer, the tray and the Claude hook.
- **git** (`filewalk.git_files`): only `ls-files`, run with `-c protocol.allow=never -c
  core.fsmonitor=false` and `GIT_NO_LAZY_FETCH=1` (`offline.GIT_OFFLINE`), so no transport and no
  partial-clone lazy fetch can run. No network subcommands anywhere (static test).
- **Server**: `make_server` refuses any non-loopback host; there is no CLI option for one (the
  hosted shell on the lab is a separate static server). Every response carries
  `Content-Security-Policy: default-src 'self'; connect-src 'self'; img-src 'self' data: blob:;
  media-src 'self' blob:; style-src 'self' <hash>; script-src 'self'; object-src 'none';
  frame-ancestors 'none'; form-action 'self'; base-uri 'none'`, `Referrer-Policy: no-referrer`,
  `X-Content-Type-Options: nosniff`. The one inline style is Cytoscape's injected container rule,
  allowed by its sha256 (a test recomputes it from the vendored file).
- **Speech**: only voices with `localService === true` (`tours.js` `localVoices`, used by the
  picker, the default choice and the player's "voice ready" check). Chrome's "Google …" and
  Edge's "Microsoft … Online (Natural)" voices send the text away and are never used; no local
  voice = the silent timeline with the usual note.
- **Hosted page**: the deliberate exception. With no `~/.config/coderstanding/hosted.json` (and
  no `--allow-origin` / env) there is no cross-origin access at all: the **work setting**.
- **Tray**: polls `127.0.0.1:8517` through a no-proxy opener (an `HTTP_PROXY` at work never sees
  it); its "Open hosted page" entry appears only when an origin is configured.
- **Check**: `coderstanding privacy [--port N]` prints guard, bind address, helper running,
  allowed origin (none / which, and where it came from), voice and git policy, and a verdict.
  No systemd or `/proc` checks, so it runs on Windows too.
- **Tests**: unit (loopback rule), runtime in a fresh interpreter (1.1.1.1:443, example.com,
  UDP, bind 0.0.0.0, urlopen and `_socket` refused; 127.0.0.1 and `localhost` work), real CLI
  reports the guard on, a static scan of `scanner/`, `scripts/`, `viewer/` (not vendor) for
  network APIs and off-site URLs against a short commented allowlist, the CSP on every route,
  and e2e: every request over the whole run (CDP `Network.requestWillBeSent`) goes to
  127.0.0.1 / `data:` / `blob:`, and the page has the CSP.
- **Imports**: tree-sitter and the grammar packages load compiled grammars from the installed
  wheels; importing and parsing under the guard makes no socket call at all (tested).
