"""What `read` prints for one target, in any form (shared by `read`, `ref` and GET /api/ref)."""

import re
from pathlib import Path

from .apps import AppError
from .views import (file_block, file_defs, files_named, header_block, markdown_headings,
                    markdown_sections, miss, read_blocks, read_place, resolve_unit, suggest_files,
                    unit_at)
from .wordmatch import close_names

PLACE = re.compile(r"(.*\S):(\d+)(?:-(\d+))?")  # read's `path:line` and `path:first-last` targets
DOC_SECTION = re.compile(r"(.+\.(?:md|markdown))#(.+)", re.IGNORECASE)  # `doc.md#Heading`


def repo_path(app, text):
    """A path as `read` gets it (absolute, from the current folder, or repo-relative) as a path
    relative to the scanned root when it is inside it, else as written."""
    try:
        return Path(text).expanduser().resolve().relative_to(app.root.resolve()).as_posix()
    except ValueError:
        return Path(text).as_posix()


def read_file_blocks(graph, path, lines, namer):
    """A whole file asked for by path: a scanned file = its header and its definitions as targets;
    a Markdown file = its headings as targets; any other file is refused (give `path:first-last`)."""
    if path in {n.file for n in graph.nodes.values()}:
        first_def, defs = file_defs(graph, path, namer)
        block = header_block(path, lines, first_def)
        block["after"] = [f"defs: {', '.join(defs)}"] if defs else []
        return [block]
    if path.lower().endswith((".md", ".markdown")):
        heads = [f"{path}#{text}" for _, _, text in markdown_headings(lines)]
        return [{"unit": None, "file": path, "line": 1, "end": len(lines), "hint": None,
                 "ranges": [], "after": heads or ["(no headings)"]}]
    raise AppError(f"{path}: not a scanned code file; read {path}:1-{min(len(lines), 80)} "
                   "(or any other line range)")


def read_section_blocks(path, heading, lines):
    sections = markdown_sections(lines, heading)
    if not sections:
        heads = [h[2] for h in markdown_headings(lines)]
        raise miss(f"{path} has no heading {heading}",
                   [f"{path}#{t}" for t in close_names([heading], heads)])
    if len(sections) > 1:
        raise AppError(f"{path}#{heading} matches several headings; read one:\n  "
                       + "\n  ".join(f"{path}#{text}" for _, _, text in sections))
    first, last, _ = sections[0]
    return [file_block(path, first, last, lines)]


def module_file(graph, path, suffix):
    """The scanned file a module stem or name in a `stem:line` target stands for; several are
    listed as ready-to-paste targets, none is a did-you-mean miss."""
    files = files_named(graph, path)
    if len(files) > 1:
        raise AppError(f"{path} is in several files; pick one:\n  "
                       + "\n  ".join(f + suffix for f in files))
    if not files:
        raise miss(f"no file {path}", suggest_files(graph, path))
    return files[0]


def read_target_blocks(app, graph, notes, target, signatures, context, source, namer):
    """What `read` prints for one target, whatever form: `path:A-B` / `path:line`, `path` (header
    and definitions), `doc.md#Heading`, `Class#function`, `Class`, or a bare function name.
    `source(file)` = the file's lines."""
    place = PLACE.fullmatch(target)
    section = DOC_SECTION.fullmatch(target)
    path = repo_path(app, section[1] if section else place[1] if place else target)
    is_file = (app.root / path).is_file()
    if place and not is_file and not unit_at(graph, path, int(place[2])):
        path, is_file = module_file(graph, path, target[len(place[1]):]), True  # `resolver:230-300`
    if place and is_file and place[3]:
        return [file_block(path, int(place[2]), int(place[3]), source(path))]
    if place and not place[3] and (is_file or unit_at(graph, path, int(place[2]))):
        return read_place(graph, notes, path, int(place[2]), signatures, context)
    if section and is_file:
        return read_section_blocks(path, section[2], source(path))
    if is_file:
        return read_file_blocks(graph, path, source(path), namer)
    if place or section or "/" in target:
        raise miss(f"no file {path}", suggest_files(graph, path))
    return read_blocks(graph, notes, resolve_unit(graph, notes, target), signatures,
                       context)
