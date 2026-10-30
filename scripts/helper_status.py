"""Pure status logic for the helper tray: no Qt, no network. Tested by tests/test_helper_status.py."""

UP, BUSY, DOWN = "up", "busy", "down"
COLORS = {UP: "#2e9e4f", BUSY: "#e0a020", DOWN: "#8a8a8a"}


def _scanning(listing):
    return [app["name"] for app in listing.get("apps", []) if app.get("scanning")]


def icon_state(listing):
    """listing = parsed /api/apps, or None when the helper did not answer."""
    if listing is None:
        return DOWN
    return BUSY if _scanning(listing) else UP


def tooltip(listing):
    if listing is None:
        return "Coderstanding helper: down"
    apps = listing.get("apps", [])
    parts = ["Coderstanding helper: up", f"{len(apps)} app{'' if len(apps) == 1 else 's'}"]
    if _scanning(listing):
        parts.append("scanning " + ", ".join(_scanning(listing)))
    errors = [f"{app['name']}: {app['error']}" for app in apps if app.get("error")]
    if errors:
        parts.append("error " + "; ".join(errors))
    return " · ".join(parts)
