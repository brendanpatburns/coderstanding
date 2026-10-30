"""Godot project and scene reader (docs/DESIGN.md 4.4).

project.godot: autoloads (global names + autoload/singleton flags), main scene (entry).
*.tscn: one `scene` node; composition (scene-child) to attached scripts and sub-scenes;
[connection] -> signal edge from the listening script to the emitter.
"""

import posixpath

from .. import paths
from ..filewalk import list_files
from ..godot_files import autoloads, main_scene, read_config, read_scene
from ..model import entry_flags
from ..language import Declaration, FileResult, LanguageModule, Mark, Ref, Target, register
from .gdscript import declared_class_name, script_id

AUTOLOAD_FLAGS = [*entry_flags("autoload"), "singleton"]
UID_PREFIX = "uid://"


class GodotFiles:
    """res:// and uid:// lookups for one file inside a Godot project."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.godot_dir = ctx.godot_root()

    def rel(self, res_path):
        return paths.res_to_rel(res_path, self.godot_dir)

    def read(self, res_path):
        path = self.ctx.root / self.rel(res_path)
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    def script_node_id(self, res_path):
        """Node id the GDScript reader gives this script, or "" if it is not a readable .gd."""
        if not res_path.endswith(".gd"):
            return ""
        source = self.read(res_path)
        if not source:
            return ""
        return script_id(declared_class_name(source), paths.rel_to_res(self.rel(res_path),
                                                                         self.godot_dir))

    def scene_by_uid(self, uid):
        for rel in list_files(self.ctx.root, ("*.tscn",)):
            res = paths.rel_to_res(rel, self.godot_dir)
            if read_scene(self.read(res)).uid == uid:
                return res
        return ""


def read_project(ctx):
    result = FileResult()
    files = GodotFiles(ctx)
    config = read_config(ctx.read_text())
    for name, path in autoloads(config).items():
        target = Target(path=path)
        result.global_aliases[name] = target
        result.marks.append(Mark(target, list(AUTOLOAD_FLAGS)))
    scene = main_scene(config)
    if scene.startswith(UID_PREFIX):
        scene = files.scene_by_uid(scene)
    if scene:
        result.marks.append(Mark(Target(path=scene), entry_flags("godot-main-scene")))
        root = read_scene(files.read(scene)).root
        if root and root.script:
            result.marks.append(Mark(Target(path=root.script), entry_flags("godot-main")))
    return result


def read_scene_file(ctx):
    scene = read_scene(ctx.read_text())
    scene_id = ctx.res_path()
    root = scene.root
    decl = Declaration(id=scene_id, name=posixpath.basename(scene_id),
                       kind="scene", base=root.type if root else "", file_node=True)
    result = FileResult(declarations=[decl])
    for node in scene.nodes:
        for child in (node.script, node.instance):
            if child:
                result.refs.append(Ref(scene_id, Target(path=child), "composition",
                                       node.line, "scene-child"))
    files = GodotFiles(ctx)
    for connection in scene.connections:
        ref = connection_ref(scene, scene_id, files, connection)
        if ref:
            result.refs.append(ref)
    return result


def emitter_target(node):
    """What emits a signal: the node's script, else its sub-scene, else its engine type."""
    if node.script:
        return Target(path=node.script)
    if node.instance:
        return Target(path=node.instance)
    return Target(name=node.type) if node.type else None


def connection_ref(scene, scene_id, files, connection):
    """signal edge listener -> emitter; the scene stands in for a listener without a script."""
    source, listener = scene.node(connection.source), scene.node(connection.target)
    target = emitter_target(source) if source else None
    if target is None or listener is None:
        return None
    from_id = files.script_node_id(listener.script) if listener.script else ""
    label = f"{connection.signal} → {connection.method}"
    return Ref(from_id or scene_id, target, "signal", connection.line, connection.signal,
               label, auto=f"signal {label}")


@register
class GodotModule(LanguageModule):
    name = "godot"
    patterns = ("project.godot", "*.tscn")

    def parse(self, ctx):
        if ctx.rel_path.rsplit("/", 1)[-1] == "project.godot":
            return read_project(ctx)
        return read_scene_file(ctx)
