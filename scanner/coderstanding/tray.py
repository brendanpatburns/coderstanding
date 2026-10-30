"""System-tray icon for the Coderstanding local helper (StatusNotifierItem via QSystemTrayIcon).

Run by `coderstanding tray` (needs Qt: `pip install "coderstanding[tray]"`). Polls the helper's
/api/apps; starting, stopping and the log are `helper.py`'s (systemd on Linux, Startup entry on
Windows). Status wording and icon state live in helper_status.py."""
import sys

from . import helper, helper_status as status
from .userconfig import hosted_origin

try:
    from PyQt6.QtCore import QTimer, QUrl
    from PyQt6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
    from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
except ImportError:
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .offline import install as install_offline_guard

install_offline_guard()

HOSTED_URL = hosted_origin()  # from the user's hosted.json; None: no menu entry
POLL_MS = 5000


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


class Tray:
    def __init__(self, app):
        self.icons = {s: make_icon(s) for s in status.COLORS}
        self.tray = QSystemTrayIcon(self.icons[status.DOWN], app)
        self.menu = QMenu()
        if HOSTED_URL:
            self._add("Open hosted page", lambda: QDesktopServices.openUrl(QUrl(HOSTED_URL)))
        self._add("Open local viewer", lambda: QDesktopServices.openUrl(QUrl(helper.LOCAL_URL)))
        self.menu.addSeparator()
        self._add("Start helper", lambda: helper.control("start"))
        self._add("Restart helper", lambda: helper.control("restart"))
        self._add("Stop helper", lambda: helper.control("stop"))
        self._add("Show log", helper.show_log)
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
        listing = helper.fetch_listing()
        self.tray.setIcon(self.icons[status.icon_state(listing)])
        self.tray.setToolTip(status.tooltip(listing))


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    tray = Tray(app)  # noqa: F841 (keeps the tray alive)
    sys.exit(app.exec())
