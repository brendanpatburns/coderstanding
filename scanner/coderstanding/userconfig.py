"""The one place that decides where the user's own files live, per platform, and the per-user
settings read from the config folder (host names never live in the code: `hosted.json` =
`{"origin": "<hosted page origin>"}`).

Linux: config `~/.config/coderstanding`, data `~/.local/share/coderstanding`, log
`~/.local/state/coderstanding`. Windows: config `%APPDATA%\\coderstanding`, data
`%LOCALAPPDATA%\\coderstanding\\data`, log `%LOCALAPPDATA%\\coderstanding`.

Stdlib only and imports nothing else from the package (the tray script uses it too).
"""

import json
import os
import sys
from pathlib import Path

ALLOW_ORIGIN_ENV = "CODERSTANDING_ALLOW_ORIGIN"  # overrides hosted.json's origin


def windows():
    """True on Windows (tests patch this to build the other platform's paths)."""
    return sys.platform == "win32"


def _windows_dir(variable, fallback):
    return Path(os.environ.get(variable) or Path.home() / fallback) / "coderstanding"


def config_dir():
    """Read at call time, so a changed HOME (tests) is honoured."""
    if windows():
        return _windows_dir("APPDATA", "AppData/Roaming")
    return Path.home() / ".config" / "coderstanding"


def data_home():
    """The parent of the default data folders (one per app)."""
    if windows():
        return _windows_dir("LOCALAPPDATA", "AppData/Local") / "data"
    return Path.home() / ".local" / "share" / "coderstanding"


def log_dir():
    """Where the helper writes its log file (Linux uses the journal instead)."""
    if windows():
        return _windows_dir("LOCALAPPDATA", "AppData/Local")
    return Path.home() / ".local" / "state" / "coderstanding"


def hosted_origin():
    """The hosted page's origin (env first, then hosted.json), or None when there is none."""
    origin = os.environ.get(ALLOW_ORIGIN_ENV)
    if origin:
        return origin
    path = config_dir() / "hosted.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("origin") or None
    except (OSError, ValueError, AttributeError):
        return None
