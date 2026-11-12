"""The local helper (`coderstanding serve`) on demand, per platform: the one module that knows what
each platform's entries contain, and how to install, remove, start, stop and inspect them.

Nothing runs at login; the helper runs only while a page uses it (`serve --idle-exit`, DESIGN v3)
and shows the tray icon while it runs (`serve --tray`, when Qt is installed).
Linux: a systemd user socket (`coderstanding-helper.socket`, enabled at login, not a process) that
starts `coderstanding-helper.service` on the first connection, plus a `coderstanding://` link
handler (.desktop file). Windows: the `coderstanding://` link handler in the user's registry
(HKCU, no admin rights); the page's Start helper button opens it. `pythonw` keeps it windowless.
The platform switch is `backend()`; everything else (CLI, tray) calls the functions below.
"""

import atexit
import importlib.util
import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import assets, userconfig
from .procs import popen_quiet, run_quiet

PORT = 8517
HELPER = "coderstanding-helper"
TRAY = "coderstanding-tray"
LOCAL_URL = f"http://127.0.0.1:{PORT}/"
# No proxy, even when HTTP_PROXY is set: the poll goes straight to loopback, never to a
# (company) proxy (DESIGN 18).
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
IDLE_EXIT = 10  # seconds without an open page before the helper exits
# The one way a helper is started (unit, link, `helper start`): idle exit and the tray icon.
SERVE_ARGS = ["serve", "--port", str(PORT), "--idle-exit", str(IDLE_EXIT), "--tray"]
WINDOWS_SERVE_ARGS = [*SERVE_ARGS, "--service"]  # --service: log to the log file, write the pid file
# Requests carrying this header (tray polls, CLI checks) never keep an idle helper up.
MONITOR_HEADER = "X-Coderstanding-Monitor"
SCHEME = "coderstanding"  # coderstanding://start: the page's Start helper link
LINK_ARGS = ["helper", "url-start"]  # what the link runs; the URL itself is never used
DETACHED_FLAGS = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
LOG_BYTES, LOG_FILES = 1_000_000, 3
VERIFY_TRIES = 8  # one try per second
NEXT_STEPS = (f"Add a project: coderstanding add <name> <folder>, or open {LOCAL_URL} "
              "-> Projects... -> Add project...")


# --- how to run this installation --------------------------------------------------------------

def launch_command(windowless=False):
    """argv that runs this installation's CLI: its `coderstanding` entry point, else
    `python -m coderstanding`. `windowless` (Windows only): use pythonw so no console appears."""
    python = Path(sys.executable)
    if userconfig.windows() and windowless:
        pythonw = python.with_name("pythonw.exe")
        return [str(pythonw if pythonw.exists() else python), "-m", "coderstanding"]
    for name in ("coderstanding.exe", "coderstanding"):
        entry = python.with_name(name)  # same bin folder: the entry point of this very install
        if entry.exists():
            return [str(entry)]
    found = shutil.which("coderstanding")
    return [found] if found else [str(python), "-m", "coderstanding"]


def launch_env():
    """Only a source checkout (nothing installed) needs help to find the package."""
    return {} if assets.is_installed() else {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}


def pipx_managed(prefix=None):
    """True when this install lives in a pipx-managed venv (pipx_metadata.json inside it, or the
    venv sits in a `pipx/venvs` folder)."""
    prefix = Path(prefix or sys.prefix)
    in_pipx_venvs = prefix.parent.name == "venvs" and prefix.parent.parent.name.lower() == "pipx"
    return in_pipx_venvs or (prefix / "pipx_metadata.json").exists()


def tray_install_hint(prefix=None, python=None):
    """The command that adds Qt to the environment this install actually runs in."""
    if pipx_managed(prefix):
        return "pipx inject coderstanding PySide6"
    return f'"{python or sys.executable}" -m pip install "coderstanding[tray]"'


def qt_available():
    return any(importlib.util.find_spec(name) for name in ("PyQt6", "PySide6"))


# --- platforms ---------------------------------------------------------------------------------

def systemd_quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def cmd_quote(text):
    return '"' + text.replace("%", "%%") + '"'


def desktop_quote(text):
    """One Exec= word of a .desktop file (Desktop Entry spec: quote, backslash-escape, %%)."""
    escaped = "".join("\\" + ch if ch in '"`$\\' else ch for ch in text)
    return '"' + escaped.replace("\\", "\\\\").replace("%", "%%") + '"'


class SystemdUser:
    """Linux: user units under ~/.config/systemd/user and a link handler in
    ~/.local/share/applications."""
    name = "systemd"
    SOCKET, SERVICE = f"{HELPER}.socket", f"{HELPER}.service"
    DESKTOP = f"{SCHEME}-url.desktop"

    def folder(self):
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
        return Path(base) / "systemd" / "user"

    def apps_folder(self):
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        return Path(base) / "applications"

    def _exec(self, *args):
        return "ExecStart=" + " ".join(systemd_quote(word) for word in [*launch_command(), *args])

    def _desktop_exec(self):
        env = [f"{k}={v}" for k, v in launch_env().items()]
        words = [*(["env", *env] if env else []), *launch_command(), *LINK_ARGS]
        # `--` and %u last: whatever the URL holds is one ignored argument, never an option.
        return "Exec=" + " ".join(desktop_quote(word) for word in words) + " -- %u"

    def files(self):
        env = "".join(f"Environment={systemd_quote(f'{k}={v}')}\n" for k, v in launch_env().items())
        socket_unit = (f"[Unit]\nDescription=Coderstanding local helper socket (127.0.0.1:{PORT})\n\n"
                       f"[Socket]\nListenStream=127.0.0.1:{PORT}\n\n[Install]\nWantedBy=sockets.target\n")
        # No [Install]: the service never starts at login, only through the socket.
        service = (f"[Unit]\nDescription=Coderstanding local helper (serve on 127.0.0.1:{PORT})\n"
                   f"Requires={self.SOCKET}\nAfter={self.SOCKET}\n\n"
                   f"[Service]\n{self._exec(*SERVE_ARGS)}\n{env}Restart=on-failure\nRestartSec=5\nNice=10\n")
        desktop = ("[Desktop Entry]\nType=Application\nName=Coderstanding helper\n"
                   "Comment=Starts the Coderstanding local helper (coderstanding:// links)\n"
                   f"{self._desktop_exec()}\nMimeType=x-scheme-handler/{SCHEME};\n"
                   "NoDisplay=true\nTerminal=false\n")
        return {self.folder() / self.SOCKET: socket_unit, self.folder() / self.SERVICE: service,
                self.apps_folder() / self.DESKTOP: desktop}

    def old_entries(self):
        """Login entries of older installs: the login-started helper's link, the tray unit."""
        return [self.folder() / "default.target.wants" / self.SERVICE,
                self.folder() / "graphical-session.target.wants" / f"{TRAY}.service",
                self.folder() / f"{TRAY}.service"]

    def after_install(self):
        # stop: an old login-started helper (or a running one) frees the port for the socket.
        # One unit per stop: systemctl refuses the whole stop when any unit is not loaded
        # (the old tray unit is gone by now).
        commands = [self._ctl("daemon-reload"), self._ctl("stop", self.SERVICE),
                    self._ctl("stop", f"{TRAY}.service"), self._ctl("enable", self.SOCKET)]
        if shutil.which("xdg-mime"):
            commands.append(["xdg-mime", "default", self.DESKTOP, f"x-scheme-handler/{SCHEME}"])
        return commands

    def before_uninstall(self):
        return [self._ctl("disable", "--now", self.SOCKET, self.SERVICE, f"{TRAY}.service")]

    def after_uninstall(self):
        return [self._ctl("daemon-reload")]

    def all_files(self):
        return [*self.files(), *self.old_entries()]

    def _ctl(self, *args):
        return ["systemctl", "--user", *args]

    def status_lines(self):
        lines = []
        for unit in (self.SOCKET, self.SERVICE):
            if not (self.folder() / unit).exists():
                lines.append(f"{unit}: not installed")
                continue
            state = [self._answer("is-enabled", unit), self._answer("is-active", unit)]
            lines.append(f"{unit}: {', '.join(state)}")
        link = (self.apps_folder() / self.DESKTOP).exists()
        return [*lines, f"{SCHEME}:// link: {'installed' if link else 'not installed'}"]

    def _answer(self, query, unit):
        done = run_quiet(self._ctl(query, unit), capture_output=True, text=True)
        return done.stdout.strip() or done.stderr.strip() or "unknown"

    def control(self, action):
        """start: listen on the socket (the helper itself starts with the first page);
        stop: close the socket and stop the helper (nothing listens until start, a
        coderstanding://start link or the next login)."""
        units = [self.SOCKET] if action == "start" else [self.SOCKET, self.SERVICE]
        run_quiet(self._ctl(action, *units), check=False)

    def recent_log(self, lines):
        """(last log lines, where the whole log is)"""
        done = run_quiet(["journalctl", "--user", "-u", HELPER, "-n", str(lines), "--no-pager"],
                              capture_output=True, text=True, check=False)
        return done.stdout.strip(), f"journalctl --user -u {HELPER}"

    def show_log(self):
        follow = f"journalctl --user -u {HELPER} -f"
        for terminal, flag in (("konsole", ["-e"]), ("gnome-terminal", ["--"]), ("xterm", ["-e"])):
            if shutil.which(terminal):
                popen_quiet([terminal, *flag, "bash", "-c", follow])
                return
        print(f"no terminal found; run: {follow}")


def windows_quote(text):
    return '"' + text + '"'


class WindowsUser:
    """Windows: the coderstanding:// link handler under HKCU\\Software\\Classes (no admin rights),
    written with reg.exe. The helper starts from the link or `helper start`, never at login."""
    name = "windows"
    KEY = rf"HKCU\Software\Classes\{SCHEME}"

    def startup_folder(self):
        appdata = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

    def link_command(self):
        """The registry's open command. The URL (%1) is left out: nothing from it reaches a
        command line. A checkout (no entry point) sets PYTHONPATH through cmd first."""
        command = " ".join(windows_quote(w) for w in [*launch_command(windowless=True), *LINK_ARGS])
        env = "".join(f'set "{k}={v}"&& ' for k, v in launch_env().items())
        return f'cmd.exe /d /s /c "{env}{command}"' if env else command

    def files(self):
        return {}

    def old_entries(self):
        """Startup `.cmd` files of older installs (helper and tray at login)."""
        return [self.startup_folder() / f"{unit}.cmd" for unit in (HELPER, TRAY)]

    def after_install(self):
        return [["reg", "add", self.KEY, "/ve", "/d", "URL:Coderstanding helper", "/f"],
                ["reg", "add", self.KEY, "/v", "URL Protocol", "/d", "", "/f"],
                ["reg", "add", rf"{self.KEY}\shell\open\command", "/ve", "/d", self.link_command(), "/f"]]

    def before_uninstall(self):
        return [["reg", "delete", self.KEY, "/f"]]

    def after_uninstall(self):
        return []

    def all_files(self):
        return self.old_entries()

    def pid_file(self):
        return userconfig.log_dir() / "helper.pid"

    def log_file(self):
        return userconfig.log_dir() / "helper.log"

    def status_lines(self):
        done = run_quiet(["reg", "query", self.KEY], capture_output=True, text=True, check=False)
        link = "installed" if done.returncode == 0 else "not installed"
        return [f"{SCHEME}:// link: {link}", f"log: {self.log_file()}"]

    def control(self, action):
        if action in ("stop", "restart"):
            self._stop()
        if action in ("start", "restart"):
            popen_quiet([*launch_command(windowless=True), *WINDOWS_SERVE_ARGS], flags=DETACHED_FLAGS,
                             env={**os.environ, **launch_env()}, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def recent_log(self, lines):
        try:
            text = self.log_file().read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        return "\n".join(text.splitlines()[-lines:]), str(self.log_file())

    def _stop(self):
        try:
            pid = int(self.pid_file().read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        run_quiet(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        self.pid_file().unlink(missing_ok=True)

    def show_log(self):
        os.startfile(self.log_file())  # Windows only; opens the default text editor


def backend():
    """The one platform switch."""
    return WindowsUser() if userconfig.windows() else SystemdUser()


# --- actions (CLI and tray call these) ---------------------------------------------------------

def _run(commands, dry_run):
    for command in commands:
        print(("would run: " if dry_run else "running: ") + " ".join(command))
        if not dry_run:
            run_quiet(command, check=False)


def _remove(paths, dry_run):
    for path in paths:
        if path.exists() or path.is_symlink():
            print(("would remove " if dry_run else "removed ") + str(path))
            if not dry_run:
                path.unlink()


def install(dry_run=False, platform=None, start=True):
    """Write the on-demand entries (Linux socket + service + link handler, Windows link handler),
    remove older installs' login entries (helper and tray at login), then start the helper and
    check it answers (`start` False: leave it stopped). Returns False when the helper was started
    but did not answer."""
    platform = platform or backend()
    for path, text in platform.files().items():
        print(("would write " if dry_run else "wrote ") + str(path))
        if dry_run:
            print("".join(f"    {line}\n" for line in text.splitlines()), end="")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
    _remove(platform.old_entries(), dry_run)
    _run(platform.after_install(), dry_run)
    if not qt_available():
        print(f"No tray icon (needs Qt: {tray_install_hint()}; the helper shows it once Qt is there).")
    if dry_run:
        return True
    if not start:
        print("Not started. Start now with: coderstanding helper start")
        return True
    running = start_unless_running(platform)
    if running:
        print(NEXT_STEPS)
    return running


def uninstall(dry_run=False, platform=None):
    platform = platform or backend()
    _run(platform.before_uninstall(), dry_run)
    _remove(platform.all_files(), dry_run)
    _run(platform.after_uninstall(), dry_run)


def start_from_link(*_url):
    """`helper url-start` (the coderstanding:// link): start the helper unless one answers.
    Whatever the URL holds is ignored; it never reaches a command."""
    if not helper_running():
        backend().control("start")


def start_tray(qt=None):
    """`serve --tray`: the tray icon as a child that quits when this helper exits (its stdin
    closes). None without Qt. A second tray quits by itself (tray.py's lock)."""
    if not (qt_available() if qt is None else qt):
        return None
    return popen_quiet([*launch_command(windowless=True), "tray", "--attached"], stdin=subprocess.PIPE,
                       env={**os.environ, **launch_env()})


def fetch_listing(opener=None, port=PORT):
    """The helper's parsed /api/apps, or None when it does not answer."""
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/apps", headers={MONITOR_HEADER: "1"})
    try:
        with (opener or LOCAL_OPENER).open(request, timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def helper_running(port=PORT, opener=None):
    """True when a Coderstanding helper answers on the port (the one "is the helper up" check)."""
    listing = fetch_listing(opener, port)
    return isinstance(listing, dict) and "apps" in listing


def start_unless_running(platform=None, **verify_options):
    """Start the helper and verify it answers, unless one already does. Returns True when up."""
    if helper_running():
        print(f"helper already running on {LOCAL_URL}")
        return True
    (platform or backend()).control("start")
    return verify_running(platform=platform, **verify_options)


def wait_until_down(pause=time.sleep, tries=10):
    """Wait (a second per try) until nothing answers on the helper port; False if it never frees."""
    for _ in range(tries):
        if not helper_running():
            return True
        pause(1)
    return False


def verify_running(fetch=None, pause=time.sleep, tries=VERIFY_TRIES, platform=None):
    """Wait a few seconds for the helper to answer; print the result. On failure show the last
    log lines and the log's location. Returns True when it answered."""
    fetch = fetch or (lambda: True if helper_running() else None)
    for attempt in range(tries):
        if fetch() is not None:
            print(f"helper running on {LOCAL_URL}")
            return True
        if attempt < tries - 1:
            pause(1)
    lines, where = (platform or backend()).recent_log(10)
    print(f"helper did not start: nothing answers on {LOCAL_URL}")
    if lines:
        print("last log lines:\n" + "".join(f"    {line}\n" for line in lines.splitlines()), end="")
    print(f"log: {where}")
    return False


def status():
    from .helper_status import tooltip
    print("\n".join([*backend().status_lines(), tooltip(fetch_listing())]))


def control(action, verify=False):
    """action: start, stop or restart the helper. `verify` (the CLI): after start/restart check it
    answers and report; returns False when it did not."""
    if action == "restart":
        backend().control("stop")
        wait_until_down()
    if action in ("start", "restart") and verify:
        return start_unless_running()
    backend().control("start" if action == "restart" else action)
    return True


def show_log():
    backend().show_log()


# --- Windows service mode (`serve --service`) --------------------------------------------------

class LogStream:
    """A text stream into a rotating log file, for pythonw (which has no console)."""

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=LOG_BYTES, backupCount=LOG_FILES, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self.pending = ""

    def write(self, text):
        self.pending += text
        *lines, self.pending = self.pending.split("\n")
        for line in lines:
            self.handler.emit(logging.makeLogRecord({"msg": line}))
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def enter_service_mode():
    """Send stdout/stderr to the rotating log file and record our pid (so `helper stop` finds us).
    Returns the console's stderr it replaced, or None when there is no console (pythonw)."""
    console = sys.stderr
    log = WindowsUser()
    sys.stdout = sys.stderr = LogStream(log.log_file())
    pid_file = log.pid_file()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(lambda: pid_file.unlink(missing_ok=True))
    return console


def report_service_failure(error, console):
    """A startup error in service mode: into the log, and onto the console when there is one."""
    message = f"error: {error}"
    print(message)  # stdout/stderr are the log file here
    if console is not None:
        print(message, file=console, flush=True)


def log_line(text):
    """One line into the helper log (service mode, before it owns the log and pid file)."""
    stream = LogStream(WindowsUser().log_file())
    stream.write(text + "\n")
