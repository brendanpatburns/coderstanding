# Agent notes

- Read `CLAUDE.md`, `docs/DESIGN.md`, and `docs/COMPONENTS.md` first.
- DRY: each piece of logic lives in one place. Extend an existing component before adding
  one. Shared scanner steps (file walk, resolver, model) are used by every language module;
  language modules never re-implement them.
- Collapse, concrete-view and zoom logic lives only in the view engine (`scanner/coderstanding/views.py`).
  The viewer and text CLI call it; they never compute views themselves.
- Update `docs/COMPONENTS.md` in the same commit when you add or widen a component.
- Add tests for your work and run only those test files.
- Work on your own branch; do not merge to main or push.
- Be token-efficient: grep, read line ranges, terse report.
- Product UI text says AI, not Claude; stored data keeps the real author name.

## How to write a language module

A language module only reads files. It never resolves names, builds edges, walks the repo,
or guesses levels: the shared core does that (`scanner/coderstanding/`, see COMPONENTS.md).

1. Add one file in `scanner/coderstanding/languages/` (auto-loaded; no core edits needed):
   ```python
   from coderstanding.language import FileResult, LanguageModule, register

   @register
   class CSharpModule(LanguageModule):
       name = "csharp"            # written as node "lang"
       patterns = ("*.cs",)       # glob on the file name, or on the path if it has "/"
       path_suffixes = ("",)      # tried after path refs, e.g. ("", ".ts", "/index.ts")
       loose_names = False        # True: unknown names may match any id ending ".Name"

       def parse(self, ctx):      # ctx: FileContext (rel_path, read_text(), res_path(), godot_root())
           return FileResult(...)
   ```
2. `FileResult` (all in `language.py`):
   - `declarations`: one `Declaration(id, name, kind, line, flags, namespace, base, members,
     file_node)` per type. You choose the id (C# full name, GDScript `class_name` or
     `ctx.res_path()`, Python dotted module path, JS repo path). Nested/child ids use
     `model.child_id(parent, name)` (`parent.name`). Same id in several files merges (partial).
     Set `file_node=True` on the one node a path reference to this file means.
     `base` is the first base as written (info only); flags from `model.NODE_FLAGS`
     (`entry` marks an entry point / main scene for the level guess).
   - `refs`: `Ref(from_id, to=Target(...), kind, line, detail, call, scope, auto)` for every
     edge-making reference, including bases (`generalization`; the resolver turns it into
     `realization` when the target is an `interface` node). One Ref per occurrence; the
     core merges same from/to/kind and counts calls. Calls through a held field: kind
     `association` with detail `call`; it folds into a `composition` edge of the same pair.
   - `from_member` on a Ref (optional, default None): name of the enclosing function/method
     (lambdas and local functions count as their enclosing one), `language.INIT_MEMBER`
     (`<init>`) for initializers and top-level code, `language.FIELD_MEMBER` (`<field>`) for a
     field's type reference; None for bases, imports and scene files. The core counts calls
     per (from_member, member) pair. Bump `cache.SCANNER_VERSION` when a reader's output changes.
   - `Target`: exactly one of `name` (as written, e.g. `List`'s arg `Foo`, `Outer.Inner`),
     `path` (`res://x.gd`, `./util`, repo-relative), or `id`; optional `member` for a child
     (`import {Foo} from './a'` = `Target(path="./a", member="Foo")`).
     Optional `scope`: the scope an alias target's name was written in (C++ `using`).
   - `scope` on a Ref: dotted enclosing scope searched innermost-out (C#: `NS.Outer`).
   - `imports`: prefixes tried next (C# `using`s). `aliases`: file-local name -> Target
     (GDScript `const X := preload(...)`, Python/JS imports). `global_aliases`: names for all
     files (Godot autoloads); their paths resolve relative to the declaring file.
   - `marks`: `Mark(Target, flags)` to flag a node declared elsewhere (autoload, entry).
3. Do not filter outside names: emit them as normal refs. Unresolved ones become the
   node's `external_refs` (for the stack reader); ambiguous ones get `unsure`.
4. Strip generics/arrays yourself: emit one Ref per type name (`Dictionary<K, Foo>` -> `Foo`).
5. Raise on a broken file if you must: the scan warns and skips that file.
6. Tests: write fixture files under `tmp_path` and call
   `coderstanding.scan.scan_repo(root, modules=[YourModule()])`; see `tests/fake_lang.py`.
   Run with `.venv/bin/pytest tests/<your file>` (pytest config puts `scanner/` on the path).
