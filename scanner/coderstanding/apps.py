"""Registered apps (`~/.config/coderstanding/apps.json`), their data files, and name lookup.

An app is a scanned repo. Its graph.json and notes.jsonl live in `<repo>/.coderstanding/`
unless a data folder is given. Text commands rescan when a scanned file is newer than
graph.json.
"""

import json
import os
import posixpath
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import userconfig
from .cache import SCANNER_VERSION
from .checks import apply_note_warnings, check_layer_sides, parse_layer, parse_rule
from .graph_json import read_graph, write_graph
from .layout import FILE_NAME as LAYOUT_FILE
from .levels import AUTO, DEFAULT_MAX_LEVEL, MAX_LEVELS, parse_hub_threshold, parse_max_level
from .notes import DOMAIN_PREFIX, domain_id, load_notes, save_notes
from .scan import scan_repo
from .testcode import is_test


class AppError(Exception):
    pass


def parse_setting(name, text):
    """A scan setting's value from text, or AppError (unknown name, bad value)."""
    if name not in SETTINGS:
        raise AppError(f"unknown setting {name} (known: {', '.join(SETTINGS)})")
    try:
        return SETTINGS[name][0](text)
    except ValueError as error:
        raise AppError(str(error)) from error


# Per-app scan settings, kept in `<data>/settings.json`: name -> (parser, default).
SETTINGS = {"hub_threshold": (parse_hub_threshold, AUTO), "max_level": (parse_max_level, DEFAULT_MAX_LEVEL)}
RELEVEL_SCOPES = ("ai", "all")  # which per-class level notes `rescan --relevel` clears


NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# What a data folder holds: files Coderstanding makes (safe to delete with an app) and files the
# user and AI wrote (deleted only when asked by name). tour_code.json: left by the dropped lab sync.
GENERATED_FILES = ("graph.json", "cache", LAYOUT_FILE, "tour_code.json")
OWN_FILES = ("notes.jsonl", "specs", "memos", "tours", "settings.json", "fields.md")

REGISTRY_ENV = "CODERSTANDING_REGISTRY"  # other registry file (tests, a second profile)
DATA_HOME_ENV = "CODERSTANDING_DATA_HOME"  # other parent of the default data folders


def registry_path():
    other = os.environ.get(REGISTRY_ENV)
    return Path(other) if other else userconfig.config_dir() / "apps.json"


def default_data_dir(name):
    """Where a new app's graph, notes and cache go unless told otherwise: out of the repo."""
    home = os.environ.get(DATA_HOME_ENV)
    base = Path(home) if home else userconfig.data_home()
    return base / name


def load_registry():
    path = registry_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def clean_name(name):
    """A valid app name (letters, digits, . _ -), or AppError."""
    name = str(name or "").strip()
    if not NAME_PATTERN.fullmatch(name):
        raise AppError("name: letters, digits, . _ - only (max 64)")
    return name


def save_registry(apps):
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(apps, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def register(name, repo, data=None):
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise AppError(f"not a directory: {repo}")
    apps = load_registry()
    apps[name] = {"path": str(repo), **({"data": str(Path(data).resolve())} if data else {})}
    save_registry(apps)
    return apps[name]


@dataclass
class App:
    name: str
    root: Path
    data: Path

    @property
    def graph_path(self):
        return self.data / "graph.json"

    @property
    def notes_path(self):
        return self.data / "notes.jsonl"

    @property
    def cache_dir(self):
        return self.data / "cache"

    @property
    def settings_path(self):
        return self.data / "settings.json"

    def saved_settings(self):
        path = self.settings_path
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def write_settings(self, **changes):
        """Save changes next to the other saved settings (scan settings and rules share the file)."""
        self.data.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps({**self.saved_settings(), **changes}, indent=1) + "\n", encoding="utf-8")

    def scan_settings(self, overrides=None):
        """Every setting, defaults filled in. `overrides` ({name: text}, validated, never saved)
        win for this call only."""
        saved = self.saved_settings()
        settings = {name: saved.get(name, default) for name, (_, default) in SETTINGS.items()}
        for name, text in (overrides or {}).items():
            settings[name] = parse_setting(name, text)
        return settings

    def change_settings(self, changes):
        """Validate and save {name: text}; they apply at the next rescan."""
        changes = dict(changes)
        names = changes.pop("levels", None)  # "Domains,App,Systems": level 0 first; not a scan setting
        if names is not None:
            names = [n.strip() for n in names.split(",")]
            self.set_levels(len(names) - 1, names)
        settings = self.scan_settings(changes)
        self.write_settings(**settings)
        return settings

    def level_settings(self):
        """The saved {count, names} of the depth ruler, or None for the scan's own levels.
        Like rules, a change never rescans."""
        return self.saved_settings().get("levels")

    def set_levels(self, count, names):
        """Save how many levels the app has (1..MAX_LEVELS, not counting level 0) and their names."""
        try:
            count = int(count)
        except (TypeError, ValueError) as error:
            raise AppError("levels: count must be a number") from error
        if not 1 <= count <= MAX_LEVELS:
            raise AppError(f"levels: count must be 1-{MAX_LEVELS}")
        names = [str(n).strip() for n in (names or [])][:count + 1]
        levels = {"count": count, "names": names}
        self.write_settings(levels=levels)
        return levels

    def reset_levels(self):
        """Back to the scan's own levels, named "Level N"."""
        settings = self.saved_settings()
        settings.pop("levels", None)
        self.settings_path.write_text(json.dumps(settings, indent=1) + "\n", encoding="utf-8")

    def warning_rules(self):
        """Per-folder warning rules (checks.parse_rule). They only filter warnings on load,
        so unlike scan settings a change never rescans."""
        return self.saved_settings().get("rules", [])

    def add_rule(self, glob, ignore, **filters):
        try:
            rule = parse_rule(glob, ignore, **filters)
        except ValueError as error:
            raise AppError(str(error)) from error
        self.write_settings(rules=[*self.warning_rules(), rule])
        return rule

    def loaded_rules(self):
        """"Loaded by" pattern rules (checks.parse_loaded_rule), e.g. every class in
        `languages/*.py` is loaded by the registry; like warning rules, no rescan."""
        return self.saved_settings().get("loaded_by", [])

    def layer_rules(self):
        """Architecture rules (checks.parse_layer); like warning rules, a change never rescans."""
        return self.saved_settings().get("layers", [])

    def add_layer(self, from_, to, mode, kind=None, why=None):
        """Save an architecture rule; unknown domains/tags and globs matching nothing are errors."""
        try:
            layer = parse_layer(from_, to, mode, kind, why)
            check_layer_sides(layer, load_graph(self), self.load_notes())
        except ValueError as error:
            raise AppError(str(error)) from error
        self.write_settings(layers=[*self.layer_rules(), layer])
        return layer

    def remove_rule(self, number):
        """Remove rule N (1-based, as `rules` lists them: warning rules, then architecture rules)."""
        rules, layers = self.warning_rules(), self.layer_rules()
        if not 1 <= number <= len(rules) + len(layers):
            raise AppError(f"no rule {number} (have {len(rules) + len(layers)})")
        if number <= len(rules):
            removed = rules.pop(number - 1)
            self.write_settings(rules=rules)
        else:
            removed = layers.pop(number - len(rules) - 1)
            self.write_settings(layers=layers)
        return removed

    def load_notes(self):
        notes = load_notes(self.notes_path)
        notes.rules = self.warning_rules()
        notes.layers = self.layer_rules()
        notes.levels = self.level_settings()
        notes.loaded_rules = self.loaded_rules()
        return notes

    def checked(self, graph, notes):
        """Put the `layer` warnings on the graph (they need the notes' domains and tags)."""
        apply_note_warnings(graph, notes.layers, notes)

    def save_notes(self, notes):
        save_notes(notes, self.notes_path)


def find_app(name_or_path, data=None):
    """A registered name, else a repo folder. A relative registry `data` is relative to the
    registry file's folder (the hub's data root: `<root>/apps.json`, `<root>/<app>/`)."""
    entry = load_registry().get(name_or_path)
    if entry:
        root, data = Path(entry["path"]), data or entry.get("data")
        data = data and registry_path().parent / data
        name = name_or_path
    else:
        root = Path(name_or_path).resolve()
        if not root.is_dir():
            raise AppError(f"unknown app (not registered, not a folder): {name_or_path}")
        name = root.name
    return App(name, root, Path(data) if data else root / ".coderstanding")


def registered_app(name):
    """A registered app by its exact registry name (no folder fallback), or AppError."""
    if name not in load_registry():
        raise AppError(f"unknown app: {name}")
    return find_app(name)


def size_of(path):
    """Bytes in a file or folder (0 if missing)."""
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.is_dir() else 0


def data_files(app):
    """{"generated": [...], "own": [...]}: names of GENERATED_FILES / OWN_FILES present in the data folder."""
    return {kind: [name for name in names if (app.data / name).exists()]
            for kind, names in (("generated", GENERATED_FILES), ("own", OWN_FILES))}


def old_scanner(version):
    """True for a graph.json another scanner version wrote (None: no graph yet)."""
    return version is not None and version != SCANNER_VERSION


def app_details(app, facts):
    """The app manager's facts about an app; `facts` = graph_json.graph_summary of its graph.json."""
    version = facts.get("scannerVersion")
    tours = app.data / "tours"
    return {"scannerVersion": version, "staleScanner": old_scanner(version),
            "graphBytes": app.graph_path.stat().st_size if app.graph_path.exists() else None,
            "dataBytes": size_of(app.data), "notes": len(load_notes(app.notes_path).keys()),
            "tours": len(list(tours.glob("*.md"))) if tours.is_dir() else 0,
            "files": data_files(app)}


def check_deletable(app):
    """Refuse to delete in a data folder that is (or holds) the repo, or sits in the repo anywhere
    but `<repo>/.coderstanding`: deleting `cache/` there could hit the user's own files."""
    data, root = app.data.resolve(), app.root.resolve()
    if data == root or data in root.parents:
        raise AppError(f"the data folder {data} holds the repo; delete its files by hand")
    if root in data.parents and data != root / ".coderstanding":
        raise AppError(f"the data folder {data} is inside the repo; delete its files by hand")


def remove_app(name, delete_data=False, delete_notes=False):
    """Unregister an app. delete_data: also delete what Coderstanding made in its data folder
    (GENERATED_FILES); delete_notes (only with delete_data): also the user's OWN_FILES there.
    Never touches anything outside the data folder. Returns {removed, deleted, kept}."""
    app = registered_app(name)
    if delete_notes and not delete_data:
        raise AppError("deleting notes needs deleting the scan data too")
    names = (GENERATED_FILES + (OWN_FILES if delete_notes else ())) if delete_data else ()
    if names:
        check_deletable(app)
    deleted = []
    for entry in names:
        path = app.data / entry
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
        else:
            continue
        deleted.append(entry)
    if deleted and app.data.is_dir() and not any(app.data.iterdir()):
        app.data.rmdir()
    apps = load_registry()
    del apps[name]
    save_registry(apps)
    kept = [entry for entry in GENERATED_FILES + OWN_FILES if (app.data / entry).exists()]
    return {"removed": name, "data": str(app.data), "deleted": deleted, "kept": kept}


def rename_app(old, new):
    """Rename an app in the registry; its repo and data folder stay where they are."""
    registered_app(old)
    new = clean_name(new)
    apps = load_registry()
    if new in apps:
        raise AppError(f"the name {new} is already taken")
    apps[new] = apps.pop(old)
    save_registry(apps)
    return new


def rescan(app, overrides=None):
    """Scan the repo now into the app's data folder (graph.json and cache, never the repo).
    `overrides`: {setting: text} for this run only (never saved)."""
    graph = scan_repo(app.root, cache_dir=app.cache_dir, **app.scan_settings(overrides))
    if not any(node.kind != "external" for node in graph.nodes.values()):
        raise AppError(f"no supported files found in {app.root}")
    write_graph(graph, app.graph_path)
    return graph


def relevel(app, scope="ai", reset_names=False):
    """Opt-in "guess the levels again" (after `rescan`): clear the per-class `level` notes (scope
    "ai": AI drafts only; "all": human ones too) so the scan's fresh guess shows. The saved
    level count and names stay unless `reset_names`. The cleared notes are first written to
    `notes.levels-<time>.bak.jsonl` in the data folder. Returns {"cleared": n, "backup": path or None}."""
    if scope not in RELEVEL_SCOPES:
        raise AppError(f"relevel must be one of {', '.join(RELEVEL_SCOPES)}, got {scope}")
    notes = app.load_notes()
    removed = notes.clear_field("level", ai_only=scope == "ai")
    backup = None
    if removed:
        backup = app.data / f"notes.levels-{datetime.now():%Y%m%d-%H%M%S}.bak.jsonl"
        backup.write_text("".join(json.dumps(line) + "\n" for line in removed), encoding="utf-8")
        app.save_notes(notes)
    if reset_names:
        app.reset_levels()
    return {"cleared": len(removed), "backup": backup}


def load_graph(app):
    """The app's saved graph, from any scanner version. Never scans (DESIGN 4: manual refresh only):
    a project never scanned, or with an unknown graph.json format, raises AppError saying how to scan it."""
    if not app.graph_path.exists():
        raise AppError(f"{app.name} was never scanned: press Refresh, or run `coderstanding rescan {app.name}`")
    try:
        return read_graph(app.graph_path)
    except ValueError as error:  # an unknown graph.json format; other scanner versions load
        raise AppError(f"{app.name}: {error}: run `coderstanding rescan {app.name}`") from error


def ends_with_part(text, query):
    return text == query or text.endswith(("." + query, "/" + query))


# Ways a short name can point at a node, most exact first.
NAME_RULES = (
    lambda node, q, fold: fold(node.name) == q,
    lambda node, q, fold: fold(posixpath.splitext(node.name)[0]) == q,  # `main` for main.gd
    lambda node, q, fold: ends_with_part(fold(node.id), q),  # Ns.Class, res://src/net.gd
    lambda node, q, fold: ends_with_part(fold(node.file), q),  # src/net.gd, net.gd
)


def preferred(graph, ids):
    """Of several matches, outer types over inner ones, then code over tests."""
    for keep in (lambda n: n.kind != "inner", lambda n: not is_test(n)):
        ids = [i for i in ids if keep(graph.nodes[i])] or ids
    return ids


def candidates(graph, query):
    """Ids a short name matches, by the most exact rule that matches anything."""
    for fold in (str, str.lower):
        for rule in NAME_RULES:
            found = sorted(i for i, n in graph.nodes.items() if rule(n, fold(query), fold))
            if found:
                return preferred(graph, found)
    return []


def find_id(graph, notes, query):
    """Full id, domain, or a unique short name; AppError lists candidates if ambiguous."""
    if query in graph.nodes:
        return query
    domains = notes.domains()
    if query.startswith(DOMAIN_PREFIX) and query[len(DOMAIN_PREFIX):] in domains:
        return query
    found = candidates(graph, query)
    if len(found) > 1:
        raise AppError(f"{query} is ambiguous: {', '.join(found)}")
    if found:
        return found[0]
    if query in domains:
        return domain_id(query)
    raise AppError(f"no class named {query}")


def parse_target(graph, notes, text):
    """("edge", A, B) for `A>B`/`A→B`, ("function", id, fn) for `Name#fn`, else ("node", id);
    names resolved to ids."""
    for sep in ("→", ">"):
        if sep in text:
            from_name, to_name = text.split(sep, 1)
            return "edge", find_id(graph, notes, from_name), find_id(graph, notes, to_name)
    name, sep, function = text.rpartition("#")
    if sep and name and not graph.nodes.get(text):
        return "function", find_id(graph, notes, name), function
    return "node", find_id(graph, notes, text)


def selection_id(graph, notes, text):
    """The view's id of a selection: a node id, an edge key `A→B`, or `id#function`."""
    kind, *ids = parse_target(graph, notes, text)
    return {"edge": "→", "function": "#"}.get(kind, "").join(ids)
