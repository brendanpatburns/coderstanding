"""The helper on demand: idle exit (`serve --idle-exit`), the page heartbeat, tray/CLI polls that
never keep it up, adopting systemd's passed socket, the coderstanding:// link's argument guard and
the tray the helper starts. Free ports only; no real units, registry or tray."""

import json
import socket
import subprocess
import threading
import time
from urllib.request import Request, urlopen

import pytest

from coderstanding import cli, helper, server
from coderstanding.hub import Hub
from coderstanding.server import HOST, IdleWatch, exit_when_idle, make_server, systemd_socket


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


# --- the idle rule -----------------------------------------------------------------------------

def test_exits_after_the_window_when_no_page_ever_comes():
    clock = Clock()
    watch = IdleWatch(10, clock=clock)
    clock.now += 9.9
    assert not watch.idle()
    clock.now += 0.1
    assert watch.idle()


def test_page_calls_keep_it_up_monitor_calls_do_not():
    clock = Clock()
    watch = IdleWatch(10, clock=clock)
    clock.now += 8
    watch.begin(page=True)
    watch.end(page=True)  # heartbeat at 8 s
    clock.now += 9
    for _ in range(3):  # tray polls
        watch.begin(page=False)
        watch.end(page=False)
    assert not watch.idle()
    clock.now += 1
    assert watch.idle()  # 10 s after the last page call, tray polls notwithstanding


def test_never_idle_during_a_request_or_a_scan():
    clock, scanning = Clock(), [True]
    watch = IdleWatch(10, busy=lambda: scanning[0], clock=clock)
    clock.now += 60
    assert not watch.idle()  # a scan runs
    scanning[0] = False
    watch.begin(page=True)  # a long upload
    clock.now += 60
    assert not watch.idle()
    watch.end(page=True)  # its end counts as the page's last call
    assert not watch.idle()
    clock.now += 10
    assert watch.idle()


def test_hub_is_busy_while_a_scan_runs():
    hub = Hub(None, fixed={})
    assert not hub.busy()
    hub.scans["game"] = {"state": "running"}
    assert hub.busy()
    hub.scans["game"] = {"state": "done"}
    assert not hub.busy()


# --- the real server ---------------------------------------------------------------------------

def get(base, path, headers=None):
    with urlopen(Request(base + path, headers=headers or {}), timeout=5) as response:
        return json.load(response)


def test_server_counts_pages_and_skips_monitors():
    srv = make_server(Hub(None, fixed={}), port=0, idle_exit=10)
    seen = []
    watch = srv.idle_watch
    real_begin = watch.begin
    watch.begin = lambda page: (seen.append(page), real_begin(page))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://{HOST}:{srv.server_port}"
    try:
        assert get(base, "/api/alive") == {"alive": True}
        assert helper.fetch_listing(port=srv.server_port) == {"initial": None, "apps": []}
        assert seen == [True, False]  # the heartbeat is a page, the tray/CLI poll is not
        deadline = time.monotonic() + 2  # the handler ends its request just after replying
        while watch.active and time.monotonic() < deadline:
            time.sleep(0.01)
        assert watch.active == 0
    finally:
        srv.shutdown()
        srv.server_close()


def test_no_idle_watch_without_the_flag():
    srv = make_server(Hub(None, fixed={}), port=0)
    try:
        assert srv.idle_watch is None and srv.RequestHandlerClass.func.idle_watch is None
    finally:
        srv.server_close()


def test_idle_server_stops_and_closes_the_tray_first():
    srv = make_server(Hub(None, fixed={}), port=0, idle_exit=0.3)
    order = []
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    started = time.monotonic()
    exit_when_idle(srv, srv.idle_watch, tick=0.05, before_exit=lambda: order.append("tray"))
    thread.join(5)
    assert not thread.is_alive() and order == ["tray"]
    assert time.monotonic() - started >= 0.25
    srv.server_close()


def test_a_running_scan_holds_the_exit():
    hub = Hub(None, fixed={})
    hub.scans["game"] = {"state": "running"}
    srv = make_server(hub, port=0, idle_exit=0.1)
    done = threading.Event()
    threading.Thread(target=lambda: (exit_when_idle(srv, srv.idle_watch, tick=0.05), done.set()),
                     daemon=True).start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert not done.wait(0.5)
        hub.scans["game"] = {"state": "done"}
        assert done.wait(5)
    finally:
        srv.server_close()


# --- systemd socket activation -----------------------------------------------------------------

def listening(host=HOST):
    sock = socket.socket()
    sock.bind((host, 0))
    sock.listen()
    return sock


def test_adopts_the_passed_socket_and_clears_the_variables():
    original = listening()
    env = {"LISTEN_PID": "4242", "LISTEN_FDS": "1", "LISTEN_FDNAMES": "x", "OTHER": "1"}
    passed = systemd_socket(env, pid=4242, fd=original.detach())
    assert env == {"OTHER": "1"}
    srv = make_server(Hub(None, fixed={}), sock=passed)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert srv.server_port == passed.getsockname()[1]
        assert get(f"http://{HOST}:{srv.server_port}", "/api/apps")["apps"] == []
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("env", [{}, {"LISTEN_PID": "1", "LISTEN_FDS": "1"},
                                 {"LISTEN_PID": "4242", "LISTEN_FDS": "0"}])
def test_no_socket_unless_passed_to_this_process(env):
    assert systemd_socket(dict(env), pid=4242, fd=-1) is None


def test_a_passed_network_socket_is_refused(monkeypatch):
    class Fake:
        closed = False

        def getsockname(self):
            return ("203.0.113.5", 8517)

        def close(self):
            self.closed = True
    fake = Fake()
    monkeypatch.setattr(server.socket, "socket", lambda fileno: fake)
    with pytest.raises(ValueError, match="network address"):
        systemd_socket({"LISTEN_PID": "7", "LISTEN_FDS": "1"}, pid=7)
    assert fake.closed


def test_serve_with_a_passed_socket_skips_the_second_helper_check(monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "systemd_socket", lambda: "passed")
    monkeypatch.setattr(helper, "helper_running", lambda *a: pytest.fail("would wait on its own socket"))
    monkeypatch.setattr(server, "serve", lambda app, port, **options: seen.update(options))
    cli.main(["serve", "--idle-exit", "10", "--tray"])
    assert seen == {"allow_origin": None, "idle_exit": 10.0, "sock": "passed", "tray": True}


# --- the coderstanding:// link -----------------------------------------------------------------

@pytest.mark.parametrize("url", ["coderstanding://start", "coderstanding://start?x=;rm -rf ~",
                                 "--dry-run", "uninstall", "coderstanding://stop"])
def test_link_only_ever_starts_the_helper(monkeypatch, url):
    calls = []
    monkeypatch.setattr(helper, "helper_running", lambda *a, **k: False)
    monkeypatch.setattr(helper, "backend", lambda: type("P", (), {"control": lambda self, a: calls.append(a)})())
    monkeypatch.setattr(helper, "install", lambda **k: pytest.fail("install from a URL"))
    monkeypatch.setattr(helper, "uninstall", lambda **k: pytest.fail("uninstall from a URL"))
    cli.main(["helper", "url-start", "--", url])
    assert calls == ["start"]


def test_link_does_nothing_when_a_helper_answers(monkeypatch):
    monkeypatch.setattr(helper, "helper_running", lambda *a, **k: True)
    monkeypatch.setattr(helper, "backend", lambda: pytest.fail("would start a second helper"))
    helper.start_from_link("coderstanding://start")


def test_other_actions_refuse_extra_arguments(monkeypatch):
    monkeypatch.setattr(helper, "status", lambda: pytest.fail("ran with a stray argument"))
    with pytest.raises(SystemExit):
        cli.main(["helper", "status", "extra"])


# --- the tray the helper starts ----------------------------------------------------------------

def test_tray_starts_attached_only_with_qt(monkeypatch):
    started = []
    monkeypatch.setattr(helper, "popen_quiet", lambda cmd, **k: started.append((cmd, k["stdin"])) or "proc")
    assert helper.start_tray(qt=False) is None and started == []
    assert helper.start_tray(qt=True) == "proc"
    cmd, stdin = started[0]
    assert cmd[-2:] == ["tray", "--attached"] and stdin == subprocess.PIPE


def test_tray_never_polls_once_the_helper_closed_its_pipe(monkeypatch):
    tray = pytest.importorskip("coderstanding.tray", exc_type=ImportError)
    monkeypatch.setattr(helper, "fetch_listing", lambda *a, **k: pytest.fail("a poll could wake the socket"))
    stopped = []
    fake = type("T", (), {"parent_gone": threading.Event(),
                          "timer": type("Q", (), {"stop": lambda self: stopped.append(1)})()})()
    fake.parent_gone.set()
    tray.Tray.refresh(fake)
    assert stopped == [1]
