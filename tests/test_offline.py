"""Offline guarantee (docs/DESIGN.md 18): the runtime guard, the server lockdown, and a static scan
that fails on any network API not in the short allowlist below."""

import os
import re
import subprocess
import sys
import threading
import urllib.request
from fnmatch import fnmatch
from pathlib import Path

import pytest

from coderstanding.apps import App
from coderstanding.offline import GIT_OFFLINE, is_loopback
from coderstanding.server import HOST, SECURITY_HEADERS, Api, make_server
from helpers import scan_fake

REPO = Path(__file__).resolve().parents[1]
SCANNER = REPO / "scanner"
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def run_python(code, env=None):
    """Run `code` in a fresh interpreter with the package on the path (the guard can not be
    taken off again, so runtime checks never install it in the test process)."""
    env = {**os.environ, "PYTHONPATH": str(SCANNER), **(env or {})}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, timeout=60)


# ---------- the loopback rule ----------

@pytest.mark.parametrize("host", ["127.0.0.1", "127.5.6.7", "::1", "[::1]", "::ffff:127.0.0.1",
                                  "localhost", "LOCALHOST.", "ip6-localhost", b"127.0.0.1"])
def test_loopback_allowed(host):
    assert is_loopback(host)


@pytest.mark.parametrize("host", ["1.1.1.1", "203.0.113.5", "0.0.0.0", "", "::", "fe80::1%eth0",
                                  "example.com", "localhost.example.com", "127.0.0.1.nip.io",
                                  None, 2130706433])
def test_everything_else_refused(host):
    assert not is_loopback(host)


# ---------- the runtime guard ----------

GUARD_CHECKS = r"""
import socket, threading, urllib.error, urllib.request
from coderstanding.offline import OfflineError, install
install()

def refused(what, call):
    try:
        call()
    except OfflineError as error:
        print("refused", what, "|", error)
        return
    except urllib.error.URLError as error:
        assert isinstance(error.reason, OfflineError), error
        print("refused", what, "|", error.reason)
        return
    raise SystemExit(f"NOT REFUSED: {what}")

def tcp():
    return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

refused("connect 1.1.1.1:443", lambda: tcp().connect(("1.1.1.1", 443)))
refused("connect_ex 1.1.1.1:443", lambda: tcp().connect_ex(("1.1.1.1", 443)))
refused("connect by name", lambda: tcp().connect(("example.com", 80)))
refused("udp sendto 8.8.8.8:53", lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("8.8.8.8", 53)))
refused("getaddrinfo example.com", lambda: socket.getaddrinfo("example.com", 443))
refused("gethostbyname example.com", lambda: socket.gethostbyname("example.com"))
refused("bind 0.0.0.0", lambda: tcp().bind(("0.0.0.0", 0)))
refused("create_connection", lambda: socket.create_connection(("example.com", 443), timeout=1))
refused("urlopen", lambda: urllib.request.urlopen("http://example.com/", timeout=1))
import _socket  # the C module under socket: the audit events fire there too (any OS)
refused("_socket connect", lambda: _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM).connect(("1.1.1.1", 443)))

server = tcp()
server.bind(("127.0.0.1", 0))
server.listen()
port = server.getsockname()[1]
threading.Thread(target=lambda: server.accept(), daemon=True).start()
with socket.create_connection(("127.0.0.1", port), timeout=2):
    pass
socket.getaddrinfo("localhost", port)  # Windows answers ::1 here: allowed as well
socket.getaddrinfo(None, port)
print("loopback ok")
"""


def test_guard_refuses_outside_and_allows_loopback():
    done = run_python(GUARD_CHECKS)
    assert done.returncode == 0, done.stdout + done.stderr
    assert sum(line.startswith("refused ") for line in done.stdout.splitlines()) == 10, done.stdout
    assert "1.1.1.1:443" in done.stdout and "example.com" in done.stdout  # names the destination
    assert "loopback ok" in done.stdout


def test_guard_on_in_the_real_cli():
    """`coderstanding privacy` runs through cli.main, the one entry of CLI, serve and rescan."""
    done = run_python("import sys; sys.argv = ['coderstanding', 'privacy', '--port', '1']\n"
                      "import runpy; runpy.run_module('coderstanding', run_name='__main__')",
                      env={"HOME": "/nonexistent", "USERPROFILE": "/nonexistent",
                           "CODERSTANDING_ALLOW_ORIGIN": ""})
    assert done.returncode == 0, done.stderr
    assert "network guard     on" in done.stdout
    assert "allowed origin    none" in done.stdout and "(work setting)" in done.stdout
    assert "helper not running" in done.stdout
    assert done.stdout.rstrip().endswith("Result: fully offline: nothing leaves this PC")


def test_privacy_names_an_allowed_origin():
    done = run_python("from coderstanding.cli import main; main(['privacy', '--port', '1'])",
                      env={"CODERSTANDING_ALLOW_ORIGIN": "https://code.example.org"})
    assert "https://code.example.org (from env CODERSTANDING_ALLOW_ORIGIN)" in done.stdout
    assert "Result: offline, except that the hosted page at https://code.example.org" in done.stdout


def test_tree_sitter_readers_make_no_network_calls():
    """Importing and running every reader (tree-sitter and its grammar packages) under the guard
    touches no socket at all: no download, no lookup."""
    done = run_python(r"""
import sys
events = []
sys.addaudithook(lambda event, args: events.append(event) if event.startswith("socket.") else None)
from coderstanding.offline import install
install()
from coderstanding.language import FileContext, load_builtin_modules
from coderstanding.languages.csharp import CSharpModule
modules = load_builtin_modules()
import pathlib, tempfile
root = pathlib.Path(tempfile.mkdtemp())
(root / "a.cs").write_text("namespace N { class A : B { C c; } }")
print(len(CSharpModule().parse(FileContext(root, "a.cs")).declarations), len(modules))
print("socket events:", events)
""")
    assert done.returncode == 0, done.stderr
    assert "socket events: []" in done.stdout


def test_git_runs_with_transports_off():
    assert GIT_OFFLINE == ("-c", "protocol.allow=never", "-c", "core.fsmonitor=false")
    for path in (REPO / "scanner").rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"""\[\s*["']git["']""", line):
                assert "GIT_OFFLINE" in line, f"{path.name}: git called without GIT_OFFLINE: {line}"


# ---------- the server ----------

def test_server_refuses_a_network_address():
    for host in ("0.0.0.0", "", "::", "198.51.100.7", "example.com"):
        with pytest.raises(ValueError, match="refusing to listen"):
            make_server(None, port=0, host=host)


def test_every_response_carries_the_browser_lockdown(tmp_path):
    graph = scan_fake(tmp_path / "repo", {"a.fk": "type A class\n"})
    srv = make_server(Api(App("repo", tmp_path / "repo", tmp_path / "state"), graph=graph), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        for path in ("/", "/app.js", "/api/meta", "/nope"):
            try:
                response = NO_PROXY.open(f"http://{HOST}:{srv.server_port}{path}")
            except urllib.error.HTTPError as error:  # a 404 still carries them
                response = error
            csp = response.headers["Content-Security-Policy"]
            assert csp == SECURITY_HEADERS["Content-Security-Policy"], path
            assert response.headers["Referrer-Policy"] == "no-referrer"
    finally:
        srv.shutdown()
        srv.server_close()
    for part in ("default-src 'self'", "connect-src 'self'", "script-src 'self'",
                 "style-src 'self' 'sha256-", "frame-ancestors 'none'", "base-uri 'none'"):
        assert part in csp
    assert "unsafe" not in csp and "http" not in csp


def test_csp_hash_matches_the_vendored_cytoscape_style():
    """The CSP lets in exactly Cytoscape's one inline style; a Cytoscape upgrade that changes it
    fails here (and in the e2e console check) instead of silently breaking the layout."""
    import base64
    import hashlib
    vendored = (REPO / "viewer" / "vendor" / "cytoscape.min.js").read_text(encoding="utf-8")
    assert 's="__________cytoscape_container"' in vendored
    assert 'u.textContent="."+s+" { position: relative; }"' in vendored
    style = b".__________cytoscape_container { position: relative; }"
    digest = base64.b64encode(hashlib.sha256(style).digest()).decode()
    assert f"'sha256-{digest}'" in SECURITY_HEADERS["Content-Security-Policy"]


# ---------- the static scan ----------

PY_RULES = [
    r"\b(import|from)\s+(socket|requests|httpx|aiohttp|urllib3|smtplib|ftplib|telnetlib|"
    r"xmlrpc|webbrowser|ssl)\b",
    r"urllib\.request|from\s+urllib\s+import\s+request|\burlopen\b",
    r"http\.client|from\s+http\s+import\s+client|http\.server",
    r"open_connection|create_connection|QNetwork|QDesktopServices|openUrl",
    r"""["'](curl|wget|ssh|scp|rsync)["']""",
]
JS_RULES = [
    r"\bfetch\s*\(", r"XMLHttpRequest", r"WebSocket", r"sendBeacon", r"EventSource",
    r"importScripts", r"\bimport\s*\(", r"new\s+Worker",
    r"(?:https?|wss?|ftp):\/\/[^\s\"'`)]+",          # any absolute URL ...
    r"[\"'`(=]\s*\/\/[A-Za-z0-9-]+\.[A-Za-z0-9.-]+",  # ... or a scheme-relative one
    r"@import",
]
SH_RULES = [r"\b(curl|wget|ssh|scp|rsync|ncat|nc)\b", r"git\s+(push|fetch|pull|clone|ls-remote)"]
LOOPBACK_URL = re.compile(r"(https?|wss?)://(127\.0\.0\.1|localhost)[:/]")

# (file glob, text on the line, why it is fine). Keep this short; every entry needs a reason.
ALLOWED = [
    ("scanner/coderstanding/offline.py", "import socket",
     "the guard's loopback probe for `coderstanding privacy` (127.0.0.1 only)"),
    ("scanner/coderstanding/offline.py", "create_connection",
     "same loopback probe"),
    ("scanner/coderstanding/server.py", "http.server",
     "the local server's own listening socket (loopback only, make_server refuses others)"),
    ("scanner/coderstanding/helper.py", "urllib.request",
     "the tray's poll of http://127.0.0.1:8517/api/apps, through a no-proxy opener"),
    ("scanner/coderstanding/tray.py", "QDesktopServices",
     "menu items open the local viewer (or the hosted page, if configured) in the browser"),
    ("scanner/coderstanding/tray.py", "openUrl", "same menu items"),
    ("viewer/apps.js", "fetch(CoderApps.url(path)", "every API call: this server or the local helper"),
    ("viewer/apps.js", 'fetch("hosted.json"', "relative: this page's own hosted.json"),
    ("viewer/apps.js", "fetch(`${url}/api/apps`",
     "probe of the local helper named by hosted.json (null on a local serve page)"),
    ("viewer/tour_view.js", "fetch(CoderApps.url(", "tour text through the one API helper"),
    ("viewer/app.js", "fetch(CoderApps.url(", "view text through the one API helper"),
    ("viewer/fanout.js", "http://www.w3.org/2000/svg", "SVG namespace name, never fetched"),
    ("scripts/deploy-*.sh", "",
     "the maintainer's deploy tool for the hosted page; never run by the app"),
]


def scanned_files():
    """(file, repo-relative name, rules): scanner/, scripts/ and viewer/ without vendor/."""
    def rel(path):
        return path.relative_to(REPO).as_posix()
    for path in sorted((REPO / "scanner").rglob("*.py")):
        yield path, rel(path), PY_RULES
    for path in sorted((REPO / "scripts").glob("*")):
        rules = {".py": PY_RULES, ".sh": SH_RULES}.get(path.suffix)
        if rules:
            yield path, rel(path), rules
    for path in sorted((REPO / "viewer").rglob("*")):
        if "vendor" not in path.parts and path.suffix in (".js", ".html", ".css"):
            yield path, rel(path), JS_RULES


def network_findings(files):
    findings = []
    for path, rel, rules in files:
        allowed = [snippet for file, snippet, _ in ALLOWED if fnmatch(rel, file)]
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for rule in rules:
                for match in re.finditer(rule, line):
                    if LOOPBACK_URL.match(match.group(0)):
                        continue
                    if not any(snippet in line for snippet in allowed):
                        findings.append(f"{rel}:{number}: {match.group(0)!r} in {line.strip()}")
    return findings


def test_no_network_api_outside_the_allowlist():
    assert network_findings(scanned_files()) == []


def test_the_static_scan_catches_a_new_call(tmp_path):
    """The scan itself works: planted calls to outside sites are reported, even in a file that
    has allowlisted lines."""
    planted = tmp_path / "apps.js"
    planted.write_text('fetch("https://evil.example/x"); new WebSocket("wss://e.example");\n'
                       '<link rel="stylesheet" href="//cdn.example.net/x.css">\n')
    py = tmp_path / "x.py"
    py.write_text("import requests\nfrom urllib.request import urlopen\n")
    found = network_findings([(planted, "viewer/apps.js", JS_RULES), (py, "scanner/x.py", PY_RULES)])
    assert any("fetch" in f for f in found)
    assert any("evil.example" in f for f in found)
    assert any("WebSocket" in f for f in found)
    assert any("cdn.example.net" in f for f in found)
    assert {f.split(":")[1] for f in found if "scanner/x.py" in f} == {"1", "2"}


def test_every_entry_point_installs_the_guard():
    """CLI main (also serve and the rescan child), scan workers, tray, Claude hook."""
    sources = {
        "scanner/coderstanding/cli.py": "install_offline_guard()",
        "scanner/coderstanding/scan.py": "initializer=install_offline_guard",
        "scanner/coderstanding/tray.py": "install_offline_guard()",
        "scanner/coderstanding/hook.py": "install_offline_guard()",
    }
    for rel, call in sources.items():
        assert call in (REPO / rel).read_text(encoding="utf-8"), rel
