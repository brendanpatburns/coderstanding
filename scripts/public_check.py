#!/usr/bin/env python3
"""Prepare and check the public copy of this repo (used by scripts/export-public.sh). Stdlib only.

  public_check.py prepare <tree>   drop excluded paths (the tree's public-exclude.txt) and
                                   apply the private markers below, in place
  public_check.py check <tree> [--denylist FILE]
                                   list every personal-looking string; exit 1 on any hit

Markers (whole lines, in any file; the comment style is free: <!-- -->, #, //):
  <!-- private:start --> ... <!-- private:end -->   the lines between (and the markers) are dropped
  TEXT -->                        becomes TEXT (keeping the indent)

The check uses the generic patterns below plus a denylist of the user's own strings, kept OUTSIDE
the repo (default ~/.config/coderstanding/public-denylist.txt; never commit or export it):
one string per line (case-insensitive substring), `re:<regex>` for a regex, `#` comments.
"""

import argparse
import fnmatch
import re
import sys
from pathlib import Path

EXCLUDE_FILE = "public-exclude.txt"
DEFAULT_DENYLIST = Path.home() / ".config" / "coderstanding" / "public-denylist.txt"
MAX_BYTES = 1_000_000  # bigger files are refused: check them by hand

MARKER = re.compile(r"^\s*(?:<!--|#|//)\s*private:(start|end)\s*(?:-->)?\s*$")
PUBLIC_LINE = re.compile(r"^(\s*)(?:<!--|#|//)\s*public-line: (.*?)\s*(?:-->)?\s*$")

# Generic patterns: (kind, regex). Allowed look-alikes are excluded inside the regex.
PATTERNS = [
    ("email", r"[A-Za-z0-9._%+-]+@(?!(?:users\.noreply\.github\.com|example\.(?:com|org|net))\b)"
              r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
    ("private IP", r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01])|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7]))"
                   r"\.\d{1,3}\.\d{1,3}\b"),
    ("private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("token", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}"
              r"|sk-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35})"),
    ("secret value", r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\w*\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
    ("home path", r"/home/(?!me/|user/|runner/)[A-Za-z_][\w.-]*|/Users/(?!me/|user/)[A-Za-z][\w.-]*"
                  r"|[A-Za-z]:\\Users\\|/mnt/[A-Za-z]|/media/[A-Za-z]|/run/media/[A-Za-z]"),
]
SECRET_FILES = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*",
                "*.kdbx", "public-denylist*")


def load_excludes(tree):
    path = tree / EXCLUDE_FILE
    if not path.exists():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def excluded(rel, patterns):
    return any(rel.startswith(p) if p.endswith("/") else fnmatch.fnmatchcase(rel, p) for p in patterns)


def files(tree):
    """Every file (and symlink) under tree as (repo-relative posix path, Path); never .git."""
    for path in sorted(tree.rglob("*")):
        rel = path.relative_to(tree).as_posix()
        if rel == ".git" or rel.startswith(".git/") or (path.is_dir() and not path.is_symlink()):
            continue
        yield rel, path


def read_text(path):
    """The file's text, or None for a binary file or a symlink."""
    if path.is_symlink():
        return None
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def apply_markers(text, where="text"):
    """Drop private blocks and unwrap public-line lines. Raises ValueError on a bad block."""
    out, start = [], None
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        marker = MARKER.match(line)
        if marker:
            if (marker.group(1) == "start") == (start is not None):
                raise ValueError(f"{where}:{number}: unbalanced private:{marker.group(1)}")
            start = number if marker.group(1) == "start" else None
            continue
        if start is not None:
            continue
        public = PUBLIC_LINE.match(line)
        out.append(public.group(1) + public.group(2) + "\n" if public else line)
    if start is not None:
        raise ValueError(f"{where}:{start}: private:start without private:end")
    return "".join(out)


def prepare(tree):
    """Make tree public in place: excluded paths gone, markers applied. Returns removed paths."""
    patterns = load_excludes(tree)
    removed = []
    for rel, path in list(files(tree)):
        if excluded(rel, patterns):
            path.unlink()
            removed.append(rel)
            continue
        text = read_text(path)
        if text is not None and ("private:" in text or "public-line:" in text):
            new = apply_markers(text, rel)
            if new != text:
                path.write_text(new, encoding="utf-8")
    for folder in sorted((p for p in tree.rglob("*") if p.is_dir() and ".git" not in p.parts),
                         key=lambda p: len(p.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    return removed


def load_denylist(path):
    """[(label, compiled regex)] from the user's denylist file."""
    rules = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        regex = line[3:] if line.startswith("re:") else re.escape(line)
        rules.append(("denylist", re.compile(regex, re.IGNORECASE)))
    return rules


def find_hits(tree, rules):
    """[(path, line or 0, kind, matched text)] for everything that looks personal."""
    hits = []
    for rel, path in files(tree):
        for kind, rule in rules:
            for match in rule.finditer(rel):
                hits.append((rel, 0, f"{kind} (file name)", match.group(0)))
        name = rel.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatchcase(name, p) for p in SECRET_FILES):
            hits.append((rel, 0, "secret-looking file", name))
        if path.is_symlink():
            hits.append((rel, 0, "symlink", str(path.readlink())))
            continue
        if path.stat().st_size > MAX_BYTES:
            hits.append((rel, 0, "large file", f"{path.stat().st_size} bytes"))
        text = read_text(path)
        if text is None:
            hits.append((rel, 0, "binary file", "check by hand"))
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for kind, rule in rules:
                for match in rule.finditer(line):
                    hits.append((rel, number, kind, match.group(0)))
    return hits


def generic_rules():
    return [(kind, re.compile(regex)) for kind, regex in PATTERNS]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare").add_argument("tree", type=Path)
    check = commands.add_parser("check")
    check.add_argument("tree", type=Path)
    check.add_argument("--denylist", type=Path, default=DEFAULT_DENYLIST)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        try:
            removed = prepare(args.tree)
        except ValueError as error:
            print(f"public_check: {error}", file=sys.stderr)
            return 1
        print(f"excluded {len(removed)} files: {', '.join(removed) or '-'}")
        return 0
    if not args.denylist.is_file():
        print(f"public_check: no denylist at {args.denylist}: refusing (it holds your own "
              "strings; keep it outside the repo)", file=sys.stderr)
        return 2
    hits = find_hits(args.tree, generic_rules() + load_denylist(args.denylist))
    for rel, number, kind, text in hits:
        print(f"{rel}:{number}: {kind}: {text}")
    count = sum(1 for _ in files(args.tree))
    print(f"{'REFUSED' if hits else 'clean'}: {len(hits)} hits in {count} files", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
