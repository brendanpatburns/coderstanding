"""package.json reader: the scripts a Node package runs directly (`bin`, `node x.js` scripts).

It declares nothing; it marks the JS/TS files as `script-main` entry points (tools).
"""

import json
import re

from coderstanding.language import FileResult, LanguageModule, Mark, Target, register
from coderstanding.model import entry_flags

RUNNER_RE = re.compile(r"\b(?:node|ts-node|tsx|bun|deno run)\s+(?:-\S+\s+)*(\.?[\w./-]+\.[cm]?[jt]s)\b")


def script_paths(data):
    """Files named by `bin` and by `node file.js`-style entries of `scripts`."""
    bins = data.get("bin")
    found = [bins] if isinstance(bins, str) else list(bins.values()) if isinstance(bins, dict) else []
    scripts = data.get("scripts")
    for command in (scripts.values() if isinstance(scripts, dict) else []):
        found += RUNNER_RE.findall(command) if isinstance(command, str) else []
    return [path for path in dict.fromkeys(found) if isinstance(path, str)]


@register
class PackageJsonModule(LanguageModule):
    name = "package-json"
    patterns = ("package.json",)
    path_suffixes = ("", ".js", ".mjs", ".cjs", ".ts")

    def parse(self, ctx):
        try:
            data = json.loads(ctx.read_text())
        except ValueError:
            return FileResult()
        if not isinstance(data, dict):
            return FileResult()
        return FileResult(marks=[Mark(Target(path=path if path.startswith(".") else f"./{path}"),
                                      entry_flags("script-main")) for path in script_paths(data)])
