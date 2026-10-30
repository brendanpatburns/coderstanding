#!/usr/bin/python3
"""System-tray icon for the Coderstanding local helper (StatusNotifierItem via QSystemTrayIcon).

Polls http://127.0.0.1:8517/api/apps; the helper itself is the systemd user unit
coderstanding-helper.service. Status wording and icon state live in helper_status.py."""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

try:
    from PyQt6.QtCore import QTimer, QUrl
    from PyQt6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
    from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
except ImportError:
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helper_status as status  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scanner"))
from coderstanding.userconfig import hosted_origin  # noqa: E402

LOCAL_URL = "http://127.0.0.1:8517/"
HOSTED_URL = hosted_origin()  # from ~/.config/coderstanding/hosted.json; None: no menu entry
UNIT = "coderstanding-helper"
POLL_MS = 5000


def fetch_listing():
    try:
        with urllib.request.urlopen(LOCAL_URL + "api/apps", timeout=2) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def make_icon(state):
    """A filled circle in the state's colour; a white ring marks busy."""
    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(status.COLORS[state]))
    p.setPen(QColor("white") if state == status.BUSY else QColor(0, 0, 0, 80))
    p.drawEllipse(6, 6, 52, 52)
    p.end()
    return QIcon(pix)


def systemctl(*args):
    subprocess.run(["systemctl", "--user", *args, UNIT], check=False)


def show_log():
    cmd = f"journalctl --user -u {UNIT} -f"
    subprocess.Popen(["konsole", "-e", "bash", "-c", cmd])


class Tray:
    def __init__(self, app):
        self.icons = {s: make_icon(s) for s in status.COLORS}
        self.tray = QSystemTrayIcon(self.icons[status.DOWN], app)
        self.menu = QMenu()
        if HOSTED_URL:
            self._add("Open hosted page", lambda: QDesktopServices.openUrl(QUrl(HOSTED_URL)))
        self._add("Open local viewer", lambda: QDesktopServices.openUrl(QUrl(LOCAL_URL)))
        self.menu.addSeparator()
        self._add("Start helper", lambda: systemctl("start"))
        self._add("Restart helper", lambda: systemctl("restart"))
        self._add("Stop helper", lambda: systemctl("stop"))
        self._add("Show log", show_log)
        self.menu.addSeparator()
        self._add("Quit tray", app.quit)
        self.tray.setContextMenu(self.menu)
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_MS)
        self.refresh()
        self.tray.show()

    def _add(self, text, slot):
        action = QAction(text, self.menu)
        action.triggered.connect(lambda _checked=False: slot())
        self.menu.addAction(action)

    def refresh(self):
        listing = fetch_listing()
        self.tray.setIcon(self.icons[status.icon_state(listing)])
        self.tray.setToolTip(status.tooltip(listing))


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    tray = Tray(app)  # noqa: F841 (keeps the tray alive)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
