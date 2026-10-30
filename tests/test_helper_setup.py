"""Helper install/start verification, the pipx-aware tray hint, `--no-start`, and the helper
starting with no projects registered. No real services: a fake platform and a fake fetch."""

import json
import sys
import threading
from urllib.request import urlopen

import pytest

from coderstanding import cli, helper, server
from coderstanding.hub import Hub
from coderstanding.server import HOST, Api, make_server


class FakePlatform:
    name = "fake"

    def __init__(self, tmp_path, log="line one\nline two"):
        self.dir, self.log, self.started, self.up = tmp_path, log, 0, False

    def files(self, tray):
        return {self.dir / "helper.unit": "unit text"}

    def after_install(self, tray):
        return []

    def control(self, action):
        self.started += action == "start"
        self.up = self.up or action == "start"

    def recent_log(self, lines):
        return self.log, "/the/log"


def no_wait(_):
    pass


# --- verification ------------------------------------------------------------------------------

def test_verify_success(capsys):
    assert helper.verify_running(fetch=lambda: {"apps": []}, pause=no_wait)
    assert f"helper running on {helper.LOCAL_URL}" in capsys.readouterr().out


def test_verify_waits_for_a_slow_start(capsys):
    answers = iter([None, None, {"apps": []}])
    assert helper.verify_running(fetch=lambda: next(answers), pause=no_wait)


def test_verify_failure_shows_log_lines_and_path(capsys):
    platform = FakePlatform(None, log="boom 1\nboom 2")
    assert not helper.verify_running(fetch=lambda: None, pause=no_wait, tries=2, platform=platform)
    out = capsys.readouterr().out
    assert "helper did not start" in out and "boom 2" in out and "log: /the/log" in out


def test_fetch_listing_uses_the_given_opener():
    class Opener:
        def open(self, url, timeout):
            assert url.endswith("/api/apps")
            raise OSError("down")
    assert helper.fetch_listing(Opener()) is None


# --- install ----------------------------------------------------------------------------------

def test_install_starts_verifies_and_prints_next_steps(tmp_path, monkeypatch, capsys):
    platform = FakePlatform(tmp_path)
    monkeypatch.setattr(helper, "fetch_listing",
                        lambda *a, **k: {"apps": []} if platform.up else None)
    monkeypatch.setattr(helper.time, "sleep", no_wait)
    assert helper.install(tray=False, platform=platform)
    out = capsys.readouterr().out
    assert platform.started == 1 and (tmp_path / "helper.unit").exists()
    assert "helper running on" in out and "coderstanding add <name> <folder>" in out


def test_install_reports_a_helper_that_does_not_answer(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(helper, "fetch_listing", lambda *a, **k: None)
    monkeypatch.setattr(helper.time, "sleep", no_wait)
    assert not helper.install(tray=False, platform=FakePlatform(tmp_path))
    assert "helper did not start" in capsys.readouterr().out


def test_install_no_start(tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "fetch_listing", lambda *a, **k: pytest.fail("must not poll"))
    platform = FakePlatform(tmp_path)
    assert helper.install(tray=False, platform=platform, start=False)
    assert platform.started == 0


def test_cli_install_has_no_start_flag():
    parser = cli.build_parser()
    assert parser.parse_args(["helper", "install", "--no-start"]).start is False
    assert parser.parse_args(["helper", "install"]).start is True


def test_start_verifies_but_stop_does_not(monkeypatch, tmp_path):
    platform = FakePlatform(tmp_path)
    monkeypatch.setattr(helper, "backend", lambda: platform)
    monkeypatch.setattr(helper, "fetch_listing", lambda *a, **k: None)
    monkeypatch.setattr(helper, "verify_running", lambda **k: False)
    assert helper.control("start", verify=True) is False
    assert helper.control("stop", verify=True) is True
    assert helper.control("start") is True  # the tray does not wait


# --- tray hint ---------------------------------------------------------------------------------

def test_pipx_layouts_and_plain_env(tmp_path):
    venvs = tmp_path / "pipx" / "venvs" / "coderstanding"
    venvs.mkdir(parents=True)
    other = tmp_path / "elsewhere" / "coderstanding"
    other.mkdir(parents=True)
    (other / "pipx_metadata.json").write_text("{}")
    plain = tmp_path / "venv"
    plain.mkdir()
    assert helper.pipx_managed(venvs) and helper.pipx_managed(other) and not helper.pipx_managed(plain)


def test_tray_hint_per_environment(tmp_path):
    pipx = tmp_path / "pipx" / "venvs" / "coderstanding"
    pipx.mkdir(parents=True)
    plain = tmp_path / "venv"
    plain.mkdir()
    assert helper.tray_install_hint(pipx) == "pipx inject coderstanding PySide6"
    hint = helper.tray_install_hint(plain, python="/py/python")
    assert hint == '"/py/python" -m pip install "coderstanding[tray]"'


# --- serve with no projects --------------------------------------------------------------------

@pytest.fixture
def empty_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("CODERSTANDING_REGISTRY", str(tmp_path / "apps.json"))
    monkeypatch.setenv("CODERSTANDING_DATA_HOME", str(tmp_path / "data"))


def test_serve_starts_with_no_projects(empty_registry, monkeypatch, capsys):
    class Stub:
        server_port = 0

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass
    monkeypatch.setattr(server, "make_server", lambda hub, port, allow_origin=None: Stub())
    server.serve()  # used to raise AppError("no apps registered")
    assert "no projects yet" in capsys.readouterr().out


def test_api_apps_is_empty_with_no_projects(empty_registry):
    srv = make_server(Hub(Api), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urlopen(f"http://{HOST}:{srv.server_port}/api/apps") as response:
            assert json.load(response)["apps"] == []
    finally:
        srv.shutdown()
        srv.server_close()


# --- service mode failures ---------------------------------------------------------------------

def test_service_failure_goes_to_log_and_console(capsys, monkeypatch, tmp_path):
    import io
    console = io.StringIO()
    helper.report_service_failure(RuntimeError("port busy"), console)
    assert "error: port busy" in console.getvalue()
    assert "error: port busy" in capsys.readouterr().out  # stdout = the log in service mode


def test_service_failure_without_console(capsys):
    helper.report_service_failure(RuntimeError("x"), None)
    assert "error: x" in capsys.readouterr().out


def test_serve_service_exits_nonzero_on_startup_error(monkeypatch, tmp_path, capsys):
    import io
    seen = io.StringIO()
    monkeypatch.setattr(helper, "helper_running", lambda *a: False)
    monkeypatch.setattr(helper, "enter_service_mode", lambda: seen)
    monkeypatch.setattr(server, "serve", lambda *a, **k: (_ for _ in ()).throw(OSError("port busy")))
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["serve", "--service"])
    assert exit_info.value.code == 1 and "port busy" in seen.getvalue()


# --- one helper only (fixes #2) ----------------------------------------------------------------

def test_second_server_on_the_same_port_cannot_bind():
    first = make_server(Hub(None, fixed={}), port=0)
    try:
        with pytest.raises(OSError):
            make_server(Hub(None, fixed={}), port=first.server_port)
    finally:
        first.server_close()


def test_windows_bind_is_exclusive(monkeypatch):
    options = []
    monkeypatch.setattr(server.userconfig, "windows", lambda: True)
    monkeypatch.setattr(server.socket, "SO_EXCLUSIVEADDRUSE", -5, raising=False)
    real = server.socket.socket.setsockopt
    monkeypatch.setattr(server.ThreadingHTTPServer, "server_bind", lambda self: options.append(self.allow_reuse_address))
    srv = server.LoopbackServer.__new__(server.LoopbackServer)
    srv.socket = type("S", (), {"setsockopt": lambda self, *a: options.append(a)})()
    srv.server_bind()
    assert options == [(server.socket.SOL_SOCKET, -5, 1), False]


def test_serve_exits_when_a_helper_already_answers(monkeypatch, capsys):
    monkeypatch.setattr(helper, "helper_running", lambda port=helper.PORT, opener=None: True)
    monkeypatch.setattr(server, "serve", lambda *a, **k: pytest.fail("must not start a second server"))
    cli.main(["serve"])
    assert "a helper is already running on http://127.0.0.1:8517/" in capsys.readouterr().out


def test_service_mode_logs_the_guard_and_keeps_the_pid_file(monkeypatch, tmp_path):
    logged = []
    monkeypatch.setattr(helper, "helper_running", lambda *a, **k: True)
    monkeypatch.setattr(helper, "log_line", logged.append)
    monkeypatch.setattr(helper, "enter_service_mode", lambda: pytest.fail("would replace the pid file"))
    cli.main(["serve", "--service"])
    assert logged == ["a helper is already running on http://127.0.0.1:8517/"]


def test_install_and_start_leave_a_running_helper_alone(tmp_path, monkeypatch, capsys):
    platform = FakePlatform(tmp_path)
    monkeypatch.setattr(helper, "fetch_listing", lambda *a, **k: {"apps": []})
    assert helper.install(tray=False, platform=platform)
    assert (tmp_path / "helper.unit").exists() and platform.started == 0  # entry written, no second helper
    monkeypatch.setattr(helper, "backend", lambda: platform)
    assert helper.control("start", verify=True) and platform.started == 0
    assert capsys.readouterr().out.count("helper already running") == 2


def test_a_non_coderstanding_listener_is_not_the_helper(monkeypatch):
    monkeypatch.setattr(helper, "fetch_listing", lambda *a, **k: {"something": "else"})
    assert not helper.helper_running()


def test_restart_stops_waits_for_the_port_then_starts(tmp_path, monkeypatch):
    platform = FakePlatform(tmp_path)
    actions = []
    platform.control = lambda action: (actions.append(action), setattr(platform, "up", action == "start"))
    answers = iter([True, True, False, False])  # up for two polls after stop, free, free before start
    polls = []
    monkeypatch.setattr(helper, "backend", lambda: platform)
    monkeypatch.setattr(helper, "helper_running", lambda *a, **k: (polls.append(1), platform.up if "start" in actions else next(answers))[1])
    monkeypatch.setattr(helper.time, "sleep", no_wait)
    assert helper.control("restart", verify=True)
    assert actions == ["stop", "start"] and len(polls) >= 3
