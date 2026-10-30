"""Readers for Godot's text formats: config files (project.godot, plugin.cfg) and .tscn scenes.

Shared by the Godot language module and the stack reader. Tolerant on purpose: anything
it does not understand (multi-line values, sub-resources) is skipped.
"""

import re
from dataclasses import dataclass, field

SECTION_RE = re.compile(r"^\[(\w+)(.*)\]\s*$")
KEY_RE = re.compile(r"^([\w/.\-]+)\s*=\s*(.*)$")
ATTR_RE = re.compile(r'(\w+)=("[^"]*"|\w+\("[^"]*"\)|[^\s\]]+)')
QUOTED_RE = re.compile(r'"([^"]*)"')


def unquote(value):
    """`"x"` -> x, `ExtResource("1")` -> 1, other values as written."""
    found = QUOTED_RE.search(value)
    return found.group(1) if found else value.strip()


def quoted_list(value):
    """`PackedStringArray("4.5", "Forward Plus")` -> ["4.5", "Forward Plus"]."""
    return QUOTED_RE.findall(value)


def read_config(text):
    """{section: {key: raw value}} for an INI-like Godot config. Keys before any section go
    under ""."""
    sections = {"": {}}
    current = sections[""]
    for line in text.splitlines():
        header = SECTION_RE.match(line)
        if header:
            current = sections.setdefault(header.group(1), {})
            continue
        entry = KEY_RE.match(line)
        if entry:
            current[entry.group(1)] = entry.group(2).strip()
    return sections


def autoloads(config):
    """{name: res:// path} from a parsed project.godot ("*" = enabled singleton, dropped)."""
    entries = config.get("autoload", {})
    return {name: unquote(value).lstrip("*") for name, value in entries.items()}


def main_scene(config):
    """run/main_scene of a parsed project.godot (res:// or uid://), or ""."""
    return unquote(config.get("application", {}).get("run/main_scene", ""))


@dataclass
class SceneNode:
    name: str
    path: str  # "." for the root, else "A/B" relative to the root, as [connection] writes it
    type: str = ""
    script: str = ""  # res:// path of the attached script
    instance: str = ""  # res:// path of the instanced sub-scene
    line: int = 0


@dataclass
class Connection:
    signal: str
    source: str  # node path of the emitter
    target: str  # node path of the listener
    method: str
    line: int = 0


@dataclass
class Scene:
    uid: str = ""
    nodes: list = field(default_factory=list)
    connections: list = field(default_factory=list)

    @property
    def root(self):
        return self.nodes[0] if self.nodes else None

    def node(self, path):
        return next((n for n in self.nodes if n.path == path), None)


def node_path(name, parent):
    if parent is None:
        return "."
    return name if parent == "." else f"{parent}/{name}"


def read_scene(text):
    """Parse a .tscn into its nodes (with scripts / instances) and connections."""
    scene, resources, current = Scene(), {}, None
    for line_no, line in enumerate(text.splitlines(), 1):
        header = SECTION_RE.match(line)
        if header:
            tag = header.group(1)
            attrs = {k: unquote(v) for k, v in ATTR_RE.findall(header.group(2))}
            current = read_section(scene, resources, tag, attrs, line_no)
            continue
        entry = KEY_RE.match(line)
        if current is not None and entry and entry.group(1) == "script":
            current.script = resources.get(unquote(entry.group(2)), "")
    return scene


def read_section(scene, resources, tag, attrs, line_no):
    """Record one [section]; returns the SceneNode that following properties belong to."""
    if tag == "gd_scene":
        scene.uid = attrs.get("uid", "")
    elif tag == "ext_resource":
        resources[attrs.get("id", "")] = attrs.get("path", "")
    elif tag == "node":
        node = SceneNode(attrs.get("name", ""), node_path(attrs.get("name", ""),
                                                          attrs.get("parent")),
                         attrs.get("type", ""), instance=resources.get(attrs.get("instance"), ""),
                         line=line_no)
        scene.nodes.append(node)
        return node
    elif tag == "connection":
        scene.connections.append(Connection(attrs.get("signal", ""), attrs.get("from", ""),
                                            attrs.get("to", ""), attrs.get("method", ""),
                                            line_no))
    return None


CLASS_NAME_RE = re.compile(r"^[ \t]*(?:@\w+[ \t]+)*class_name[ \t]+([A-Za-z_]\w*)", re.M)


def declared_class_name(source):
    """A GDScript file's `class_name`, or "" (scripts, scenes and the stack reader use it)."""
    found = CLASS_NAME_RE.search(source)
    return found.group(1) if found else ""
