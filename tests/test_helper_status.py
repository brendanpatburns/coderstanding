import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import helper_status as hs  # noqa: E402


def listing(*apps):
    return {"initial": None, "apps": [{"name": "mygame", "scanning": False, "error": None, **a} for a in apps]}


def test_down_when_no_answer():
    assert hs.icon_state(None) == hs.DOWN
    assert hs.tooltip(None) == "Coderstanding helper: down"


def test_up_with_app_count():
    assert hs.icon_state(listing({}, {"name": "myaddon"})) == hs.UP
    assert hs.tooltip(listing({})) == "Coderstanding helper: up · 1 app"


def test_busy_while_scanning():
    busy = listing({"scanning": True})
    assert hs.icon_state(busy) == hs.BUSY
    assert hs.tooltip(busy) == "Coderstanding helper: up · 1 app · scanning mygame"


def test_scan_error_in_tooltip():
    assert hs.tooltip(listing({"error": "no files"})).endswith("error mygame: no files")
