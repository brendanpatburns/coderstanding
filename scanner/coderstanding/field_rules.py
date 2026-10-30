"""Field rules: what each note field is for, who reads it, what goes in and what does not.

The built-in rules live in `docs/field_rules.json`. A project may override any part in
`<data>/fields.md` (next to tours and specs, so a team shares it through git); everything that
shows or checks the guidance (viewer "?" tips, `fields`, `guide`, `set` warnings) asks
`effective()`. Missing keys fall back to the built-in value.

fields.md layout (hand-editable):
    # Field rules
    ## house rules
    Free text for AI notes in this project.
    ## hint
    what: ...        (also who, not, good, bad, max, code_check, by)
"""

import json
import re

from . import assets

FILE = "fields.md"
HOUSE = "house rules"
TEXT_KEYS = ("what", "who", "not", "good", "bad")
KEYS = (*TEXT_KEYS, "max", "code_check")
YES, NO = ("on", "true", "yes", "1"), ("off", "false", "no", "0")
BLOCK_REMINDER = ("hint/description are for a new developer who is not very technical and does not "
                  "know the code: say what it is FOR and where it fits, not how it works "
                  "(no types, function names or algorithms). See `coderstanding guide`.")


def defaults():
    return json.loads((assets.docs_dir() / "field_rules.json").read_text(encoding="utf-8"))


def parse_value(key, text):
    """A `key: value` as its type; raises ValueError."""
    text = text.strip()
    if key == "by":  # who edited the section last; recorded, never part of the guidance
        return text
    if key == "max":
        if not text.isdigit():
            raise ValueError("max is a number of characters (0 = no limit)")
        return int(text)
    if key == "code_check":
        if text.lower() not in YES + NO:
            raise ValueError("code_check is on or off")
        return text.lower() in YES
    if key not in TEXT_KEYS:
        raise ValueError(f"unknown key {key} (use {', '.join(KEYS)})")
    return " ".join(text.split())


def read_overrides(data_dir):
    """({field: {key: value}}, house text) from `<data>/fields.md`; unknown names and keys are ignored."""
    path = data_dir / FILE
    if not path.is_file():
        return {}, ""
    known, fields, house, current = defaults()["fields"], {}, [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            if current in known:
                fields.setdefault(current, {})
        elif current == HOUSE:
            house.append(line)
        elif current in known and ":" in line:
            key, _, text = line.partition(":")
            try:
                fields[current][key.strip()] = parse_value(key.strip(), text)
            except ValueError:
                continue
    return {n: v for n, v in fields.items() if set(v) - {"by"}}, "\n".join(house).strip()


def write_overrides(data_dir, overrides, house):
    """Write `<data>/fields.md` (deleted when nothing is overridden)."""
    path = data_dir / FILE
    overrides = {n: v for n, v in overrides.items() if set(v) - {"by"}}
    if not overrides and not house:
        path.unlink(missing_ok=True)
        return
    lines = ["# Field rules", ""]
    if house:
        lines += [f"## {HOUSE}", house, ""]
    for name in defaults()["fields"]:
        if name in overrides:
            lines.append(f"## {name}")
            lines += [f"{k}: {'on' if v is True else 'off' if v is False else v}"
                      for k, v in overrides[name].items()]
            lines.append("")
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def effective(data_dir):
    """{"rule", "house", "fields": {name: {what, who, not, good, bad, max, code_check, custom: [keys]}}}."""
    base = defaults()
    overrides, house = read_overrides(data_dir)
    fields = {}
    for name, rules in base["fields"].items():
        mine = {k: v for k, v in overrides.get(name, {}).items() if k in KEYS}
        fields[name] = {**rules, **mine, "custom": [k for k in KEYS if k in mine]}
    return {"rule": base["rule"], "house": house, "fields": fields}


def change(data_dir, sets=None, resets=None, house=None, by=None):
    """Apply `sets` ({"hint.what": text}), `resets` (field names or "all") and a new house text
    (None = keep); returns the effective rules. Raises ValueError for bad names or values."""
    known = defaults()["fields"]
    overrides, old_house = read_overrides(data_dir)
    for name in resets or ():
        if name == "all":
            overrides, old_house = {}, ""
        elif name == HOUSE.replace(" ", "_") or name == HOUSE:
            old_house = ""
        elif name in known:
            overrides.pop(name, None)
        else:
            raise ValueError(f"unknown field {name} (use {', '.join(known)}, house_rules or all)")
    edited = set()
    for target, text in (sets or {}).items():
        name, _, key = target.partition(".")
        if name in ("house", "house_rules") and not key:
            old_house = text.strip()
        elif name in known and key:
            value = parse_value(key, text)
            if value in ("", None) or (key in TEXT_KEYS and not value):
                overrides.get(name, {}).pop(key, None)  # empty = back to the built-in value
            else:
                overrides.setdefault(name, {})[key] = value
                edited.add(name)
        else:
            raise ValueError(f"not field.key: {target} (fields: {', '.join(known)})")
    for name in edited if by else ():
        overrides[name] = {**{k: v for k, v in overrides[name].items() if k != "by"}, "by": by}
    write_overrides(data_dir, overrides, old_house if house is None else house.strip())
    return effective(data_dir)


# --- text for people and agents ---

def guide_markdown(rules):
    """The "Writing note fields" body: newcomer rule, house rules, then who/what/not per field."""
    lines = [rules["rule"], ""]
    if rules["house"]:
        lines += ["House rules for AI notes in this project:", "", rules["house"], ""]
    for name, f in rules["fields"].items():
        limit = f" Max {f['max']} characters." if f["max"] else ""
        lines += [f"- `{name}`: {f['what']}{limit}", f"  - For: {f['who']}", f"  - Not: {f['not']}",
                  f"  - Good: `{f['good']}`", f"  - Bad: `{f['bad']}`"]
    return "\n".join(lines)


def effective_text(rules):
    """`coderstanding fields`: the guide body, custom parts marked."""
    lines = [guide_markdown(rules), ""]
    custom = {n: f["custom"] for n, f in rules["fields"].items() if f["custom"]}
    lines.append("project overrides: " + ("; ".join(f"{n} ({', '.join(k)})" for n, k in custom.items())
                                          if custom else "none") + (" + house rules" if rules["house"] else ""))
    return "\n".join(lines)


def reminder_lines(rules):
    """2-3 lines for `curate`: the newcomer rule in short, plus the project's house rules."""
    lines = [f"reminder: {BLOCK_REMINDER}"]
    if rules["house"]:
        lines.append("house rules: " + " ".join(rules["house"].split()))
    return lines


# --- warnings for AI writes ---

CODE_SIGNS = (
    re.compile(r"\w\(\)|\w\([^)]*\)"),               # call(), call(x)
    re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b"),         # snake_case
    re.compile(r"\b[a-z]+[A-Z]\w*\b"),               # camelCase
    re.compile(r"::|->|=>|\w\.\w+\("),               # scope, arrow, method call
    re.compile(r"\b\w+<[\w, <>]+>"),                 # generic types
    re.compile(r"\b(?:int|str|string|bool|float|void|Dictionary|List|Array|Vector\d|Dict|Map)\b"),  # type names
)


def code_signs(text):
    """How many kinds of code-like text appear (a code fence counts as three)."""
    return (3 if "```" in text else 0) + sum(1 for sign in CODE_SIGNS if sign.search(text))


def looks_code_heavy(text):
    return code_signs(text) >= 2


def field_warnings(rules, fields):
    """Reminders for AI-written values ({field: text}): too long, or code-like where plain words belong."""
    out = []
    for name, text in fields.items():
        spec = rules["fields"].get(name)
        if not spec or not isinstance(text, str) or not text:
            continue
        if spec["max"] and len(text) > spec["max"]:
            out.append(f"{name} is {len(text)} characters (max {spec['max']}): keep it to one short plain line")
        if spec["code_check"] and looks_code_heavy(text):
            out.append(f"{name} looks like code talk: {spec['who'].split('.')[0].lower()}; say what it is "
                       f"for, not how it works")
    return out
