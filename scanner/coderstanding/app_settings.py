"""The one table of an app's settings (`<data>/settings.json`).

`coderstanding settings`, its error text and printout, `rescan --setting` and the AI guide's
settings section all read SETTINGS_TABLE; add a setting here and nowhere else.
"""

import json
from dataclasses import dataclass

from .checks import parse_loaded_rule
from .levels import AUTO, DEFAULT_MAX_LEVEL, parse_hub_threshold, parse_max_level
from .library import parse_library_rule
from .progress import check_track

# kinds: how a setting is typed and changed
NUMBER = "number"  # one value: `name=VALUE`
LIST = "list"  # comma text or a JSON list: `name=a,b`
JSON_MAP = "json map"  # a JSON object: `name={"a": "b"}`
RULE_LIST = "rule list"  # items: `name=[json list]`, `name+=ITEM`, `name-=KEY`
TRACK_LIST = "track list"  # a rule list of progress tracks (JSON objects)
LEVEL_NAMES = "level names"  # `levels=Domains,App,...` (handled by App.set_levels)
SCAN_KINDS = (NUMBER, LIST, JSON_MAP)  # change what a scan finds
ITEM_KINDS = (RULE_LIST, TRACK_LIST)
SYNTAX = {NUMBER: "{name}=VALUE", LIST: "{name}=a,b", JSON_MAP: "{name}=JSON_OBJECT",
          RULE_LIST: "{name}=JSON_LIST, {name}+=ITEM, {name}-=KEY",
          TRACK_LIST: "{name}=JSON_LIST, {name}+=JSON_OBJECT, {name}-=NAME", LEVEL_NAMES: "{name}=Name0,Name1,..."}


@dataclass(frozen=True)
class Setting:
    name: str
    kind: str
    help: str
    parse: object = None  # whole value (scan kinds) or one item (item kinds) from text or JSON
    default: object = None
    key_of: object = None  # item kinds: the text that names an item for `-=`
    replaces_same_key: bool = False  # item kinds: `+=` replaces the item with the same key

    @property
    def needs_rescan(self):
        return self.kind in SCAN_KINDS

    @property
    def syntax(self):
        return SYNTAX[self.kind].format(name=self.name)


def parse_exclude(value):
    """File globs to leave out of the scan, from a list or comma-separated text."""
    patterns = value.split(",") if isinstance(value, str) else value
    return [str(pattern).strip() for pattern in patterns if str(pattern).strip()]


def parse_function_macros(value):
    """{macro name: function name template} from a dict or JSON text (`{"REWRITE": "F_{1}"}`)."""
    macros = json.loads(value) if isinstance(value, str) and value.strip() else (value or {})
    if not isinstance(macros, dict) or not all(isinstance(template, str) for template in macros.values()):
        raise ValueError("function_macros: expected {macro: name template}, e.g. {\"REWRITE\": \"F_{1}\"}")
    return macros


def parse_domain_item(item):
    """A default-domain folder rule {files, domain} from `glob=Name` text or a dict."""
    if isinstance(item, str):
        glob, _, name = item.partition("=")
        item = {"files": glob.strip(), "domain": name.strip()}
    if not (isinstance(item, dict) and item.get("files") and item.get("domain")):
        raise ValueError("domains: expected glob=Domain name, e.g. 'game/ships/**=Ships & AI'")
    return {"files": item["files"], "domain": item["domain"]}


def parse_loaded_item(item):
    """A "loaded by" folder rule {loaded_by, files, how?} from `glob=ClassId` text or a dict."""
    if isinstance(item, str):
        glob, _, loader = item.partition("=")
        item = {"files": glob.strip(), "loaded_by": loader.strip()}
    if not isinstance(item, dict):
        raise ValueError("loaded_by: expected glob=ClassId")
    return parse_loaded_rule(item.get("loaded_by"), item.get("files"), item.get("how", ""))


def parse_track_item(item):
    """A progress track from a JSON object (text or dict), checked by progress.check_track."""
    if isinstance(item, str):
        try:
            item = json.loads(item)
        except ValueError:
            raise ValueError("tracks: expected a JSON object, e.g. "
                             "'{\"name\": \"Proven\", \"done\": {\"tag\": \"proven\"}}'") from None
    return check_track(item)


SETTINGS_TABLE = (
    Setting("hub_threshold", NUMBER, "classes with this many neighbours are level 1 hubs (N, auto or off)",
            parse_hub_threshold, AUTO),
    Setting("max_level", NUMBER, "the deepest level the level guess uses (1-12)", parse_max_level, DEFAULT_MAX_LEVEL),
    Setting("exclude", LIST, "file globs left out of the scan", parse_exclude, []),
    Setting("include", LIST, "folders or globs of git-ignored files scanned anyway", parse_exclude, []),
    Setting("function_macros", JSON_MAP, 'C macros that define functions, {"REWRITE": "F_{1}"}',
            parse_function_macros, {}),
    Setting("domains", RULE_LIST, "default domain by folder, item glob=Domain", parse_domain_item, None,
            lambda rule: rule["files"]),
    Setting("loaded_by", RULE_LIST, "who loads the classes of a folder, item glob=ClassId", parse_loaded_item,
            None, lambda rule: rule["files"]),
    Setting("library", RULE_LIST, "rules marking code as an outside library (glob, name:P, tag:T, domain:D)",
            parse_library_rule, None, lambda rule: rule),
    Setting("tracks", TRACK_LIST, "own progress tracks, item {name, of, done} (see `progress`)",
            parse_track_item, None, lambda track: track["name"], replaces_same_key=True),
    Setting("levels", LEVEL_NAMES, "names of the depth levels, level 0 first (never rescans)"),
)

BY_NAME = {setting.name: setting for setting in SETTINGS_TABLE}
NAMES = tuple(BY_NAME)


def of_kinds(kinds):
    return [setting for setting in SETTINGS_TABLE if setting.kind in kinds]


def split_operator(name):
    """("domains", "+") for `domains+=...`, ("domains", "-") for `-=`, ("domains", "=") for `=`."""
    return (name[:-1], name[-1]) if name[-1:] in ("+", "-") else (name, "=")


def parse_item_list(setting, value):
    """A whole item list from a JSON list or (for plain-text rules) comma text."""
    if isinstance(value, str):
        value = json.loads(value) if value.strip().startswith("[") else \
            [part for part in value.split(",") if part.strip()]
    if not isinstance(value, list):
        raise ValueError(f"{setting.name}: expected a list")
    return [setting.parse(item) for item in value]


def changed_item_list(setting, operator, text, current):
    """The item list after `name<operator>text` (`=` replaces, `+=` adds one, `-=` removes one)."""
    if operator == "=":
        return parse_item_list(setting, text)
    if operator == "+":
        item = setting.parse(text)
        same = (lambda other: setting.key_of(other) == setting.key_of(item)) if setting.replaces_same_key \
            else (lambda other: other == item)
        return [*(other for other in current if not same(other)), item]
    kept = [item for item in current if text.strip() != str(setting.key_of(item))]
    if len(kept) == len(current):
        have = ", ".join(str(setting.key_of(item)) for item in current) or "none"
        raise ValueError(f"{setting.name}: no item {text.strip()} to remove (have: {have})")
    return kept


def unknown_setting_text(name):
    return f"unknown setting {name} (known: {', '.join(NAMES)})"


def settings_help_lines():
    """One line per setting: syntax, help, and whether a change needs a rescan."""
    return [f"`{setting.syntax}`: {setting.help}; " + ("applies at the next rescan" if setting.needs_rescan
                                                       else "applies at once")
            for setting in SETTINGS_TABLE]


def guide_markdown():
    """The AI guide's settings list (between the settings markers)."""
    return "\n".join(f"  - {line}" for line in settings_help_lines())
