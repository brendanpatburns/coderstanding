"""The local helper (`coderstanding serve`) at login, per platform: the one module that knows what
each platform's login entries contain, and how to install, remove, start, stop and inspect them.

Linux: two systemd user units (helper, tray). Windows: two `.cmd` files in the user's Startup folder
(chosen over a Task Scheduler task because it needs no admin rights and no extra tool, only a file,
and is easy to see in Task Manager's Startup tab; `pythonw` keeps the server itself windowless).
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
SERVE_ARGS = ["serve", "--port", str(PORT)]
WINDOWS_SERVE_ARGS = [*SERVE_ARGS, "--service"]  # --service: log to the log file, write the pid file
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


class SystemdUser:
    """Linux: user units under ~/.config/systemd/user."""
    name = "systemd"

    def folder(self):
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
        return Path(base) / "systemd" / "user"

    def _exec(self, *args):
        return "ExecStart=" + " ".join(systemd_quote(word) for word in [*launch_command(), *args])

    def files(self, tray):
        env = "".join(f"Environment={systemd_quote(f'{k}={v}')}\n" for k, v in launch_env().items())
        helper = (f"[Unit]\nDescription=Coderstanding local helper (serve on 127.0.0.1:{PORT})\n\n"
                  f"[Service]\n{self._exec(*SERVE_ARGS)}\n{env}Restart=on-failure\nRestartSec=5\nNice=10\n\n"
                  "[Install]\nWantedBy=default.target\n")
        files = {self.folder() / f"{HELPER}.service": helper}
        if tray:
            files[self.folder() / f"{TRAY}.service"] = (
                "[Unit]\nDescription=Coderstanding helper tray icon\nPartOf=graphical-session.target\n"
                f"After=graphical-session.target\n\n[Service]\n{self._exec('tray')}\n{env}"
                "Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=graphical-session.target\n")
        return files

    def after_install(self, tray):
        units = [f"{HELPER}.service", *([f"{TRAY}.service"] if tray else [])]
        return [self._ctl("daemon-reload"), self._ctl("enable", *units)]

    def before_uninstall(self):
        return [self._ctl("disable", "--now", f"{HELPER}.service", f"{TRAY}.service")]

    def after_uninstall(self):
        return [self._ctl("daemon-reload")]

    def all_files(self):
        return [self.folder() / f"{unit}.service" for unit in (HELPER, TRAY)]

    def _ctl(self, *args):
        return ["systemctl", "--user", *args]

    def status_lines(self):
        lines = []
        for unit in (HELPER, TRAY):
            if not (self.folder() / f"{unit}.service").exists():
                lines.append(f"{unit}: not installed")
                continue
            state = [self._answer("is-enabled", unit), self._answer("is-active", unit)]
            lines.append(f"{unit}: {', '.join(state)}")
        return lines

    def _answer(self, query, unit):
        done = run_quiet(self._ctl(query, unit), capture_output=True, text=True)
        return done.stdout.strip() or done.stderr.strip() or "unknown"

    def control(self, action):
        run_quiet(self._ctl(action, HELPER), check=False)

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


class WindowsStartup:
    """Windows: `.cmd` files in the user's Startup folder, started at every login."""
    name = "windows"

    def folder(self):
        appdata = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

    def _script(self, what, *args):
        env = "".join(f'set "{k}={v}"\r\n' for k, v in launch_env().items())
        command = " ".join(cmd_quote(word) for word in [*launch_command(windowless=True), *args])
        return (f"@echo off\r\nrem {what} at login (written by `coderstanding helper install`)\r\n"
                f'{env}start "" {command}\r\n')

    def files(self, tray):
        files = {self.folder() / f"{HELPER}.cmd": self._script("Coderstanding local helper",
                                                                *WINDOWS_SERVE_ARGS)}
        if tray:
            files[self.folder() / f"{TRAY}.cmd"] = self._script("Coderstanding tray icon", "tray")
        return files

    def after_install(self, tray):
        return []

    def before_uninstall(self):
        return []

    def after_uninstall(self):
        return []

    def all_files(self):
        return [self.folder() / f"{unit}.cmd" for unit in (HELPER, TRAY)]

    def pid_file(self):
        return userconfig.log_dir() / "helper.pid"

    def log_file(self):
        return userconfig.log_dir() / "helper.log"

    def status_lines(self):
        lines = []
        for unit in (HELPER, TRAY):
            installed = (self.folder() / f"{unit}.cmd").exists()
            lines.append(f"{unit}: {'installed (starts at login)' if installed else 'not installed'}")
        return [*lines, f"log: {self.log_file()}"]

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
    return WindowsStartup() if userconfig.windows() else SystemdUser()


# --- actions (CLI and tray call these) ---------------------------------------------------------

def _run(commands, dry_run):
    for command in commands:
        print(("would run: " if dry_run else "running: ") + " ".join(command))
        if not dry_run:
            run_quiet(command, check=False)


def install(tray=None, dry_run=False, platform=None, start=True):
    """Write the login entries and enable them, then start the helper and check it answers
    (`start` False: leave it stopped). `tray` None = with tray when Qt is importable.
    Returns False when the helper was started but did not answer."""
    platform = platform or backend()
    with_tray = qt_available() if tray is None else tray
    for path, text in platform.files(with_tray).items():
        print(("would write " if dry_run else "wrote ") + str(path))
        if dry_run:
            print("".join(f"    {line}\n" for line in text.splitlines()), end="")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
    _run(platform.after_install(with_tray), dry_run)
    if not with_tray:
        print(f"No tray icon set up (needs Qt: {tray_install_hint()}, then run this again).")
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
    for path in platform.all_files():
        if path.exists():
            print(("would remove " if dry_run else "removed ") + str(path))
            if not dry_run:
                path.unlink()
    _run(platform.after_uninstall(), dry_run)


def fetch_listing(opener=None, port=PORT):
    """The helper's parsed /api/apps, or None when it does not answer."""
    try:
        with (opener or LOCAL_OPENER).open(f"http://127.0.0.1:{port}/api/apps", timeout=2) as response:
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
    log = WindowsStartup()
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
    stream = LogStream(WindowsStartup().log_file())
    stream.write(text + "\n")
