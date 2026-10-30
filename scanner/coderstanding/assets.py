"""Where the files that ship with Coderstanding live: the viewer (HTML/JS/CSS) and the AI docs.

Installed (wheel): they are package data next to this module (`coderstanding/viewer`,
`coderstanding/docs`). Checkout: they are `viewer/` and `docs/` at the repo root. Everything that
needs one of these folders asks here, so nothing at runtime refers to a repo path.
"""

from importlib import resources
from pathlib import Path

PACKAGE = "coderstanding"


def _shipped(name):
    """The folder `name` inside the installed package, or the same folder at the repo root."""
    inside = Path(str(resources.files(PACKAGE).joinpath(name)))
    return inside if inside.is_dir() else Path(__file__).resolve().parents[2] / name


def viewer_dir():
    return _shipped("viewer")


def docs_dir():
    """Holds FOR_CLAUDE.md and skill/SKILL.md."""
    return _shipped("docs")


def is_installed():
    """True when running from an installed package, not a source checkout."""
    return Path(str(resources.files(PACKAGE).joinpath("viewer"))).is_dir()
