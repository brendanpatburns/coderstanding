"""The AI guide and the block that points an agent at it: `coderstanding guide [--snippet] [--write FILE]`.

The snippet text lives here only. `--write` marks it with begin/end comments, so a re-run replaces
its own block and never duplicates it.
"""

from pathlib import Path

from . import app_settings, assets, field_rules

BEGIN = "<!-- coderstanding:begin -->"
END = "<!-- coderstanding:end -->"
DEFAULT_APP = "<app>"
SETTINGS_BEGIN = "<!-- settings:begin -->"
SETTINGS_END = "<!-- settings:end -->"
FIELDS_BEGIN = "<!-- field-rules:begin -->"
FIELDS_END = "<!-- field-rules:end -->"
CUSTOM_LINE = ("- This project has its own rules for writing note fields (hint, description, ...): "
               "run `coderstanding fields {app}` before using `set`.\n")
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


def between_markers(text, begin, end, body):
    """`text` with the part between the two markers replaced by `body` (unchanged without markers)."""
    start, stop = text.find(begin), text.find(end)
    if not 0 <= start < stop:
        return text
    return text[:start + len(begin)] + "\n" + body + "\n" + text[stop:]


def with_fields(text, rules):
    """`text` with the part between the field-rules markers replaced by these rules."""
    return between_markers(text, FIELDS_BEGIN, FIELDS_END, field_rules.guide_markdown(rules))


def with_settings(text):
    """`text` with the settings list between the settings markers taken from the settings table."""
    return between_markers(text, SETTINGS_BEGIN, SETTINGS_END, app_settings.guide_markdown())


def guide_text(data_dir=None):
    """The AI guide; with an app's data folder, its field section shows that project's rules."""
    text = with_settings((assets.docs_dir() / "AI_GUIDE.md").read_text(encoding="utf-8"))
    return with_fields(text, field_rules.effective(data_dir)) if data_dir else text


def snippet(app=None, custom_fields=False):
    text = SNIPPET.format(app=app or DEFAULT_APP)
    if custom_fields:
        marker = "- Everything runs locally"
        text = text.replace(marker, CUSTOM_LINE.format(app=app or DEFAULT_APP) + marker)
    return text


def marked_block(app=None, custom_fields=False):
    return f"{BEGIN}\n{snippet(app, custom_fields)}{END}\n"


def write_snippet(path, app=None, custom_fields=False):
    """Put the marked block into `path` (created if missing): replace our block, else append.
    Returns "created", "updated" or "added"."""
    path = Path(path)
    block = marked_block(app, custom_fields)
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
