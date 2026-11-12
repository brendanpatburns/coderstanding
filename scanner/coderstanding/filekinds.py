"""File kind: the second label next to a node's domain (Code, Scene, Configuration, Data, Build,
Web page, Docs), set from its file's name. The one place that maps file names to kinds."""

import os

CODE = "Code"
FILE_KINDS = (CODE, "Scene", "Configuration", "Data", "Build", "Web page", "Docs")
NON_CODE_LEVEL_STEP = 1  # supporting files start this many levels lower than code (guess_levels)
APP_STRUCTURE_KINDS = (CODE, "Scene")  # what the app is built from: never pushed down a level

# Whole file names (lowercase) first; then the ending of the name. Anything else is Code when a
# language module read it, else Data.
BY_NAME = {
    "project.godot": "Configuration", "docker-compose.yml": "Configuration",
    "docker-compose.yaml": "Configuration", "compose.yml": "Configuration",
    "compose.yaml": "Configuration", ".env": "Configuration",
    "makefile": "Build", "dockerfile": "Build", "cmakelists.txt": "Build",
    "package.json": "Build", "pyproject.toml": "Build",
}
BY_ENDING = {
    ".tscn": "Scene", ".tres": "Scene", ".scn": "Scene", ".res": "Scene",
    ".cfg": "Configuration", ".ini": "Configuration", ".toml": "Configuration",
    ".yaml": "Configuration", ".yml": "Configuration", ".env": "Configuration",
    ".json": "Data", ".csv": "Data", ".txt": "Data", ".xml": "Data",
    ".gradle": "Build", ".gradle.kts": "Build",
    ".html": "Web page", ".htm": "Web page",
    ".md": "Docs",
}


def file_kind_of(file, lang=""):
    """The kind of a file ("" for no file): by name, else by ending, else Code if a language read it."""
    if not file:
        return ""
    name = os.path.basename(file).lower()
    if name in BY_NAME:
        return BY_NAME[name]
    for ending, kind in BY_ENDING.items():
        if name.endswith(ending):
            return kind
    return CODE if lang else "Data"


def is_supporting_file(node):
    """Configuration, data, build files, web pages and docs: they support the code, so they start
    one level lower. Scenes stay: in Godot (and similar engines) they are how the app is put together."""
    return bool(node.file_kind) and node.file_kind not in APP_STRUCTURE_KINDS


def mark_file_kinds(graph):
    """Set `file_kind` on every node that has a file."""
    for node in graph.nodes.values():
        node.file_kind = file_kind_of(node.file, node.lang)
