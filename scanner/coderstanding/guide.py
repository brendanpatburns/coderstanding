"""The AI guide and the block that points an agent at it: `coderstanding guide [--snippet] [--write FILE]`.

The snippet text lives here only. `--write` marks it with begin/end comments, so a re-run replaces
its own block and never duplicates it.
"""

from pathlib import Path

from . import assets

BEGIN = "<!-- coderstanding:begin -->"
END = "<!-- coderstanding:end -->"
DEFAULT_APP = "<app>"
CURSOR_HEADER = "---\ndescription: Coderstanding code map\nalwaysApply: true\n---\n\n"

SNIPPET = """## Coderstanding (code map)
This repo is in Coderstanding as `{app}`: a local map of its classes, calls, notes, specs and tours.
- Not registered on this PC yet? `coderstanding add {app} <this repo's folder>` first.
- Start with `coderstanding graph {app} 1`; run `coderstanding guide` for the full reference.
- Prefer these over grepping or opening whole files:
  - `coderstanding find {app} <words>`: names and topics
  - `coderstanding outline {app} <Class>`: its functions with file:first-last lines
  - `coderstanding node {app} <Class>` / `zoom {app} <A> <B>`: a class, a connection
  - `coderstanding impact {app} <Class>[#function]`: dependents and tests to rerun
  - `coderstanding specs {app}` / `tours {app}`: the plan and guided walks
- Everything runs locally and offline; no code leaves this machine.
- Agent instruction files: `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`,
  `.cursor/rules/coderstanding.mdc` (or `.cursorrules`).
"""


def guide_text():
    return (assets.docs_dir() / "AI_GUIDE.md").read_text(encoding="utf-8")


def snippet(app=None):
    return SNIPPET.format(app=app or DEFAULT_APP)


def marked_block(app=None):
    return f"{BEGIN}\n{snippet(app)}{END}\n"


def write_snippet(path, app=None):
    """Put the marked block into `path` (created if missing): replace our block, else append.
    Returns "created", "updated" or "added"."""
    path = Path(path)
    block = marked_block(app)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        header = CURSOR_HEADER if path.suffix == ".mdc" else ""
        path.write_text(header + block, encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8")
    start, end = text.find(BEGIN), text.find(END)
    if 0 <= start < end:
        path.write_text(text[:start] + block + text[end + len(END):].lstrip("\n"), encoding="utf-8")
        return "updated"
    separator = "" if not text.strip() else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + separator + block, encoding="utf-8")
    return "added"
