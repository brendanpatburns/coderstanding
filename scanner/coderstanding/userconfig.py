"""The user's own config folder (`~/.config/coderstanding/`) and the per-user settings read from
it, so host names never live in the code: `hosted.json` = `{"origin": "<hosted page origin>"}`.

Stdlib only and imports nothing else from the package (the tray script uses it too).
"""

import json
import os
from pathlib import Path

ALLOW_ORIGIN_ENV = "CODERSTANDING_ALLOW_ORIGIN"  # overrides hosted.json's origin


def config_dir():
    """Read at call time, so a changed HOME (tests) is honoured."""
    return Path.home() / ".config" / "coderstanding"


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
