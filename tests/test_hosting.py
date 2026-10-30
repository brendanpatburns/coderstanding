"""The hosted page is a static shell for the local helper (docs/DESIGN.md 12b): the lab gets the
viewer plus deploy/hosted.json, never a map, and runs no Coderstanding code."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_hosted_json_names_the_helper_only_on_the_lab():
    assert json.loads((ROOT / "deploy" / "hosted.json").read_text()) == {
        "helper": "http://127.0.0.1:8517"}
    assert json.loads((ROOT / "viewer" / "hosted.json").read_text()) == {"helper": None}


