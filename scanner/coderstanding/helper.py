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
import urllib.request
from pathlib import Path

from . import assets, userconfig

PORT = 8517
HELPER = "coderstanding-helper"
TRAY = "coderstanding-tray"
LOCAL_URL = f"http://127.0.0.1:{PORT}/"
# No proxy, even when HTTP_PROXY is set: the poll goes straight to loopback, never to a
# (company) proxy (DESIGN 18).
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SERVE_ARGS = ["serve", "--port", str(PORT)]
WINDOWS_SERVE_ARGS = [*SERVE_ARGS, "--service"]  # --service: log to the log file, write the pid file
LOG_BYTES, LOG_FILES = 1_000_000, 3


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
        done = subprocess.run(self._ctl(query, unit), capture_output=True, text=True)
        return done.stdout.strip() or done.stderr.strip() or "unknown"

    def control(self, action):
        subprocess.run(self._ctl(action, HELPER), check=False)

    def show_log(self):
        follow = f"journalctl --user -u {HELPER} -f"
        for terminal, flag in (("konsole", ["-e"]), ("gnome-terminal", ["--"]), ("xterm", ["-e"])):
            if shutil.which(terminal):
                subprocess.Popen([terminal, *flag, "bash", "-c", follow])
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
            flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                     | subprocess.CREATE_NO_WINDOW)
            subprocess.Popen([*launch_command(windowless=True), *WINDOWS_SERVE_ARGS], creationflags=flags,
                             env={**os.environ, **launch_env()}, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _stop(self):
        try:
            pid = int(self.pid_file().read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
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
            subprocess.run(command, check=False)


def install(tray=None, dry_run=False, platform=None):
    """Write the login entries and enable them. `tray` None = with tray when Qt is importable."""
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
        print('No tray icon set up (needs Qt: pip install "coderstanding[tray]", then run this again).')
    print("Start now with: coderstanding helper start")


def uninstall(dry_run=False, platform=None):
    platform = platform or backend()
    _run(platform.before_uninstall(), dry_run)
    for path in platform.all_files():
        if path.exists():
            print(("would remove " if dry_run else "removed ") + str(path))
            if not dry_run:
                path.unlink()
    _run(platform.after_uninstall(), dry_run)


def fetch_listing():
    """The helper's parsed /api/apps, or None when it does not answer."""
    try:
        with LOCAL_OPENER.open(LOCAL_URL + "api/apps", timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def status():
    from .helper_status import tooltip
    print("\n".join([*backend().status_lines(), tooltip(fetch_listing())]))


def control(action):
    """action: start, stop or restart the helper."""
    backend().control(action)


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
    """Send stdout/stderr to the rotating log file and record our pid (so `helper stop` finds us)."""
    log = WindowsStartup()
    sys.stdout = sys.stderr = LogStream(log.log_file())
    pid_file = log.pid_file()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(lambda: pid_file.unlink(missing_ok=True))
