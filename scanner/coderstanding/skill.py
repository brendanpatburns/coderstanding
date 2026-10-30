"""Install the Coderstanding skill for Claude Code: SKILL.md plus AI_GUIDE.md, copied from the
package to `~/.claude/skills/coderstanding` (asking before it replaces anything)."""

import shutil
import sys
from pathlib import Path

from . import assets

SKILL_NAME = "coderstanding"


def default_destination():
    return Path.home() / ".claude" / "skills" / SKILL_NAME


def skill_files():
    """{file name in the skill folder: source file}."""
    docs = assets.docs_dir()
    return {"SKILL.md": docs / "skill" / "SKILL.md", "AI_GUIDE.md": docs / "AI_GUIDE.md"}


def confirmed(question, yes):
    if yes:
        return True
    if not sys.stdin.isatty():
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def install(destination=None, yes=False):
    """Copy the skill; returns the folder, or None when the user declined to overwrite."""
    destination = Path(destination) if destination else default_destination()
    if destination.is_symlink() or destination.exists():
        what = "a link" if destination.is_symlink() else "a folder"
        if not confirmed(f"{destination} exists ({what}). Replace it?", yes):
            return None
        if destination.is_symlink() or destination.is_file():
            destination.unlink()  # never write through a link into someone's checkout
        else:
            shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for name, source in skill_files().items():
        shutil.copyfile(source, destination / name)
    return destination
