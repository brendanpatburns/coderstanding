"""Saved class spots and pins: `.coderstanding/layout.json` (docs/DESIGN.md section 12).

One line per class, sorted by key, so parallel branches merge cleanly:
    [
    {"key": "Game", "x": 120, "y": 40, "pinned": false},
    ...
    ]
"""

import json
from pathlib import Path

FILE_NAME = "layout.json"


def layout_path(state_dir):
    return Path(state_dir) / FILE_NAME


def clean_item(item):
    """One layout entry with only the known fields, or ValueError."""
    if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item["key"]:
        raise ValueError(f"bad layout item: {item!r}")
    return {"key": item["key"], "x": round(float(item["x"]), 1),
            "y": round(float(item["y"]), 1), "pinned": bool(item.get("pinned", False))}


def read_layout(state_dir):
    """key -> item; empty when there is no layout file yet."""
    path = layout_path(state_dir)
    if not path.exists():
        return {}
    return {item["key"]: item for item in map(clean_item, json.loads(path.read_text(encoding="utf-8")))}


def write_layout(state_dir, items):
    path = layout_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(items[key], ensure_ascii=False) for key in sorted(items)]
    path.write_text("[\n" + ",\n".join(lines) + ("\n" if lines else "") + "]\n")


def update_layout(state_dir, put=(), remove=()):
    """Merge new or moved items and drop removed keys; returns the whole layout."""
    items = read_layout(state_dir)
    for item in map(clean_item, put):
        items[item["key"]] = item
    for key in remove:
        items.pop(key, None)
    write_layout(state_dir, items)
    return items
