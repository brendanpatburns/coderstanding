"""All apps one server can show: the registry list, per-app Api, scans in the background.

Only the local `serve` uses this: the hosted page on the lab is a static shell that talks to it
(docs/DESIGN.md 12b), so no map ever leaves this PC. Scans run in a niced child process (`nice -n 19 coderstanding rescan`) that writes to the
app's own data folder.
"""

import os
import sys
import threading
from pathlib import Path

from . import userconfig
from .apps import (AppError, old_scanner, app_details, clean_name, default_data_dir, find_app, load_registry,
                   register, remove_app, rename_app)
from .filewalk import assign_files
from .graph_json import graph_summary
from .language import load_builtin_modules
from .procs import run_quiet

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000  # Windows creation flag
PACKAGE_PARENT = str(Path(__file__).resolve().parents[1])  # so `-m coderstanding` finds us


def scan_command(app):
    """The low-priority child process that rescans `app` into its data folder."""
    command = [sys.executable, "-m", "coderstanding", "rescan", str(app.root), "--data", str(app.data)]
    return command if userconfig.windows() else ["nice", "-n", "19", *command]


def scan_priority_flags():
    """Windows has no `nice`: lower the priority class instead (run_quiet hides the window)."""
    return BELOW_NORMAL_PRIORITY_CLASS if userconfig.windows() else 0


def scan_in_child(app):
    """Run the low-priority rescan child and wait: {"state": "done"} or {"state": "error", "error"}.
    The one way to scan in the background (the page's Refresh/Add project and `coderstanding add`)."""
    done = run_quiet(scan_command(app), flags=scan_priority_flags(), capture_output=True, text=True,
                     env={**os.environ, "PYTHONPATH": PACKAGE_PARENT})
    if done.returncode == 0:
        return {"state": "done"}
    message = (done.stderr.strip().splitlines() or ["scan failed"])[-1]
    return {"state": "error", "error": message.removeprefix("error: ")}


def checked_folder(text):
    """An absolute, existing folder, resolved."""
    text = str(text or "").strip()
    path = Path(text).expanduser()
    if not text or not path.is_absolute():
        raise AppError("folder must be an absolute path (like /home/me/code/app or C:\\code\\app)")
    if not path.exists():
        raise AppError(f"no such folder: {path}")
    if not path.is_dir():
        raise AppError(f"not a folder: {path}")
    return path.resolve()


class Hub:
    """`make_api(app)` builds the per-app Api (injected so this module stays HTTP-free).
    `fixed`: {name: Api} instead of the registry (tests, one-off graphs)."""

    def __init__(self, make_api, initial=None, fixed=None):
        self.make_api = make_api
        self.initial = initial  # App the user asked for on the command line, or None
        self.fixed = fixed
        self.apis = dict(fixed or {})
        self.scans = {}  # name -> {"state": "running"|"done"|"error", "error": str}
        self.lock = threading.Lock()
        self.counts = {}  # graph.json path -> (mtime, graph_summary or None)

    def known_apps(self):
        """{name: App}: the registry, plus the app given on the command line."""
        if self.fixed is not None:
            return {name: api.app for name, api in self.fixed.items()}
        apps = {name: find_app(name) for name in sorted(load_registry())}
        if self.initial and self.initial.name not in apps:
            apps[self.initial.name] = self.initial
        return apps

    def app(self, name=None):
        apps = self.known_apps()
        name = name or (self.initial.name if self.initial else next(iter(apps), None))
        if name not in apps:
            raise KeyError(f"unknown app: {name}")
        return apps[name]

    def api(self, name=None):
        app = self.app(name)
        with self.lock:
            if app.name not in self.apis:
                self.apis[app.name] = self.make_api(app)
            return self.apis[app.name]

    def graph_facts(self, app):
        """graph_summary of the app's graph.json (cached by mtime); {} if never scanned or unreadable."""
        path = app.graph_path
        if not path.exists():
            return {}
        stamp = path.stat().st_mtime
        if self.counts.get(path, (None,))[0] != stamp:
            try:
                self.counts[path] = (stamp, graph_summary(path))
            except (ValueError, OSError):
                self.counts[path] = (stamp, {})
        return self.counts[path][1]

    def listing(self, details=False):
        """GET /api/apps; `details` (the app manager) adds app_details: data size, notes, tours..."""
        apps = []
        for name, app in self.known_apps().items():
            facts = self.graph_facts(app)
            scan = self.scans.get(name, {})
            apps.append({"name": name, "path": str(app.root), "data": str(app.data),
                         "scannedAt": facts.get("scannedAt"),
                         "staleScanner": old_scanner(facts.get("scannerVersion")), "nodes": facts.get("nodes"),
                         "edges": facts.get("edges"),
                         "scanning": scan.get("state") == "running",
                         "error": scan.get("error") if scan.get("state") == "error" else None,
                         **(app_details(app, facts) if details else {})})
        return {"initial": self.initial.name if self.initial else None, "apps": apps}

    def forget(self, name):
        """Drop what the hub holds for `name` (after a remove or rename)."""
        with self.lock:
            self.apis.pop(name, None)
            self.scans.pop(name, None)
            if self.initial and self.initial.name == name:
                self.initial = None

    def remove(self, name, params):
        """DELETE /api/apps/<name>?data=0|1&notes=0|1 (apps.remove_app)."""
        flag = lambda key: params.get(key, ["0"])[0] == "1"
        result = remove_app(name, delete_data=flag("data"), delete_notes=flag("notes"))
        self.forget(name)
        return {**result, **self.listing(details=True)}

    def rename(self, name, body):
        """PATCH /api/apps/<name> {"name": new} (apps.rename_app)."""
        new = rename_app(name, body.get("name"))
        self.forget(name)
        return {"renamed": [name, new], **self.listing(details=True)}

    def start_scan(self, name):
        """Rescan in the background; the listing shows progress and any error."""
        app = self.app(name)
        with self.lock:
            if self.scans.get(app.name, {}).get("state") == "running":
                raise AppError(f"{app.name} is already being scanned")
            self.scans[app.name] = {"state": "running"}
        threading.Thread(target=self.run_scan, args=(app,), daemon=True).start()
        return self.listing()

    def run_scan(self, app):
        try:
            result = scan_in_child(app)
        except OSError as error:
            result = {"state": "error", "error": str(error)}
        self.scans[app.name] = result

    def add_app(self, body):
        """Register a folder like `coderstanding add` (data default: the shared data home), then
        scan it in the background. Bad input raises AppError before anything is written."""
        name = clean_name(body.get("name"))
        root = checked_folder(body.get("path"))
        if name in self.known_apps():
            raise AppError(f"the name {name} is already taken")
        if not assign_files(root, load_builtin_modules()):
            raise AppError(f"no supported files found in {root}")
        register(name, root, body.get("data") or default_data_dir(name))
        return self.start_scan(name)
