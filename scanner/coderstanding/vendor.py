"""Which files are not the app's own code: vendored or minified libraries and the app's
`exclude` globs. The one check shared by the class-graph scan and the text index."""

import re

from .filewalk import matches

MAX_LINE_WIDTH = 1000  # a file with a longer line is minified or generated
VENDOR_DIRS = ("vendor", "third_party")
VERSIONED_NAME = re.compile(r"[-_]v?\d+(\.\d+)+\.")  # livekit-client-2.22.4.umd.js, jquery-3.7.js
BUNDLE_SUFFIXES = (".js", ".mjs", ".cjs", ".css")  # where copied-in and minified libraries live


def is_bundle(rel_path):
    """Web code, the only kind where a version in the name or one huge line means a library."""
    return rel_path.endswith(BUNDLE_SUFFIXES)


def is_vendor(rel_path):
    """Third-party or minified by name: a `vendor/` folder, `*.min.*`, a versioned library name."""
    parts = rel_path.split("/")
    name = parts[-1]
    return (any(part in VENDOR_DIRS for part in parts[:-1]) or ".min." in name
            or (is_bundle(rel_path) and VERSIONED_NAME.search(name) is not None))


def is_excluded(rel_path, exclude=()):
    """True when the app's `exclude` globs (settings.json) match the path."""
    return any(matches(rel_path, pattern) for pattern in exclude)


def is_wide(text):
    return any(len(line) > MAX_LINE_WIDTH for line in text.splitlines())


def is_skipped_by_name(rel_path, exclude=()):
    return is_vendor(rel_path) or is_excluded(rel_path, exclude)


def is_vendor_file(root, rel_path, exclude=()):
    """Name check plus, for web code, a look inside for very long lines (the graph scan's test)."""
    if is_skipped_by_name(rel_path, exclude):
        return True
    if not is_bundle(rel_path):
        return False
    try:
        return is_wide((root / rel_path).read_bytes().decode("utf-8", "replace"))
    except OSError:
        return False
