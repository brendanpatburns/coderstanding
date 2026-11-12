"""System-tray icon for the Coderstanding local helper (StatusNotifierItem via QSystemTrayIcon).

Run by `coderstanding tray` (needs Qt: `pip install "coderstanding[tray]"`). Polls the helper's
/api/apps (marked as a monitor, so it never keeps an idle helper up); stopping and the log are
`helper.py`'s. The helper starts it (`serve --tray`) with `--attached`: the tray quits when the
helper's pipe on its stdin closes, i.e. when the helper exits. One tray at a time (a lock file).
Status wording and icon state live in helper_status.py."""
import sys
import threading

from . import helper, helper_status as status
from .userconfig import hosted_origin

try:
    from PyQt6.QtCore import QLockFile, QTimer, QUrl
    from PyQt6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
    from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
except ImportError:
    from PySide6.QtCore import QLockFile, QTimer, QUrl
    from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .offline import install as install_offline_guard
from .userconfig import log_dir

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
        self.parent_gone = threading.Event()  # set when the helper that started us exits
        self.icons = {s: make_icon(s) for s in status.COLORS}
        self.tray = QSystemTrayIcon(self.icons[status.DOWN], app)
        self.menu = QMenu()
        if HOSTED_URL:
            self._add("Open hosted page", lambda: QDesktopServices.openUrl(QUrl(HOSTED_URL)))
        self._add("Open local viewer", lambda: QDesktopServices.openUrl(QUrl(helper.LOCAL_URL)))
        self.menu.addSeparator()
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
        if self.parent_gone.is_set():
            self.timer.stop()
            return
        listing = helper.fetch_listing()
        self.tray.setIcon(self.icons[status.icon_state(listing)])
        self.tray.setToolTip(status.tooltip(listing))


def quit_when_stdin_closes(app, tray):
    """`--attached`: a thread waits for EOF on stdin (the helper is exiting: it closes the pipe
    before it stops answering). From then on the tray never polls again (a poll could wake the
    systemd socket and start a new helper) and quits."""
    if sys.stdin is None:  # no pipe after all: live until stopped
        return None

    def wait():
        sys.stdin.read()
        tray.parent_gone.set()
    threading.Thread(target=wait, daemon=True).start()
    timer = QTimer()
    timer.timeout.connect(lambda: tray.parent_gone.is_set() and app.quit())
    timer.start(200)
    return timer


def main(attached=False):
    log_dir().mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(log_dir() / "tray.lock"))
    if not lock.tryLock(0):
        return  # a tray is already running
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    tray = Tray(app)  # noqa: F841 (keeps the tray alive)
    watch = quit_when_stdin_closes(app, tray) if attached else None  # noqa: F841
    code = app.exec()
    lock.unlock()
    sys.exit(code)
