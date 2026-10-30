"""Packaging: where the viewer and docs come from (checkout and installed layout), the platform
login entries (Linux units, Windows Startup files), the hook snippet, the skill install, the
platform folders, and Windows-style paths."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

from coderstanding import assets, helper, hook, skill, userconfig
from coderstanding.apps import default_data_dir
from coderstanding.paths import normalize_ref_path, rel_to_res, res_to_rel

ROOT = Path(__file__).resolve().parent.parent
SCANNER = ROOT / "scanner"


# --- viewer and docs location -------------------------------------------------------------------

def test_checkout_uses_repo_folders():
    assert assets.viewer_dir() == ROOT / "viewer" and (assets.viewer_dir() / "index.html").is_file()
    assert (assets.docs_dir() / "AI_GUIDE.md").is_file() and not assets.is_installed()


def installed_layout(tmp_path):
    """site-packages/coderstanding with viewer/ and docs/ inside, as the wheel lays it out."""
    package = tmp_path / "site" / "coderstanding"
    shutil.copytree(SCANNER / "coderstanding", package, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "viewer", package / "viewer")
    (package / "docs").mkdir()
    shutil.copy(ROOT / "docs" / "AI_GUIDE.md", package / "docs")
    return tmp_path / "site"


def run_in(site, code, tmp_path):
    env = {**os.environ, "PYTHONPATH": str(site)}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_installed_layout_uses_package_data(tmp_path):
    site = installed_layout(tmp_path)
    out = run_in(site, "from coderstanding import assets; print(assets.viewer_dir(), assets.docs_dir(), "
                       "assets.is_installed())", tmp_path)
    assert out == f"{site / 'coderstanding' / 'viewer'} {site / 'coderstanding' / 'docs'} True"
    assert (site / "coderstanding" / "viewer" / "vendor" / "cytoscape.LICENSE").is_file()
    guide = run_in(site, "from coderstanding import guide; print(guide.guide_text()[:60])", tmp_path)
    assert "guide for AI coding agents" in guide


def test_wheel_package_data_lists_the_ai_guide():
    assert '"coderstanding.docs" = ["AI_GUIDE.md"]' in (ROOT / "pyproject.toml").read_text()


def test_server_serves_the_resolved_viewer(tmp_path):
    from coderstanding.server import make_server
    from coderstanding.hub import Hub
    server = make_server(Hub(None, fixed={}), port=0)
    try:
        assert server.RequestHandlerClass.keywords["directory"] == str(assets.viewer_dir())
    finally:
        server.server_close()


# --- helper entries -----------------------------------------------------------------------------

@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    """An installed copy: entry point next to the python, nothing refers to a repo."""
    bin_dir = tmp_path / "my env" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "coderstanding").write_text("")
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    monkeypatch.setattr(assets, "is_installed", lambda: True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return bin_dir


def test_systemd_units_use_the_entry_point_with_quoted_paths(fake_install):
    files = helper.SystemdUser().files(tray=True)
    names = {path.name: text for path, text in files.items()}
    entry = fake_install / "coderstanding"
    assert f'ExecStart="{entry}" "serve" "--port" "8517"' in names["coderstanding-helper.service"]
    assert f'ExecStart="{entry}" "tray"' in names["coderstanding-tray.service"]
    assert "PYTHONPATH" not in "".join(names.values())
    assert all(path.parent.name == "user" for path in files)
    assert "coderstanding-tray.service" not in {p.name for p in helper.SystemdUser().files(tray=False)}


def test_systemd_quote_escapes():
    assert helper.systemd_quote('a "b" %h $x\\') == '"a \\"b\\" %%h $$x\\\\"'


def test_windows_startup_files(fake_install, monkeypatch, tmp_path):
    scripts = tmp_path / "Py Env" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "pythonw.exe").write_text("")
    monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
    monkeypatch.setattr(userconfig, "windows", lambda: True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    files = {path.name: text for path, text in helper.WindowsStartup().files(tray=True).items()}
    assert set(files) == {"coderstanding-helper.cmd", "coderstanding-tray.cmd"}
    assert f'start "" "{scripts / "pythonw.exe"}" "-m" "coderstanding" "serve" "--port" "8517" "--service"' \
        in files["coderstanding-helper.cmd"]
    assert '"tray"' in files["coderstanding-tray.cmd"] and "\r\n" in files["coderstanding-tray.cmd"]
    startup = next(iter(helper.WindowsStartup().files(False))).parent
    assert startup.parts[-3:] == ("Start Menu", "Programs", "Startup")


def test_backend_is_chosen_by_platform(monkeypatch):
    monkeypatch.setattr(userconfig, "windows", lambda: True)
    assert isinstance(helper.backend(), helper.WindowsStartup)
    monkeypatch.setattr(userconfig, "windows", lambda: False)
    assert isinstance(helper.backend(), helper.SystemdUser)


def test_install_dry_run_writes_nothing(fake_install, tmp_path, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    helper.install(tray=True, dry_run=True)
    out = capsys.readouterr().out
    assert "would write" in out and "would run: systemctl --user enable" in out
    assert calls == [] and not (tmp_path / "home" / ".config" / "systemd").exists()


def test_install_and_uninstall_files(fake_install, tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: ran.append(cmd))
    helper.install(tray=True)
    unit_dir = tmp_path / "home" / ".config" / "systemd" / "user"
    assert sorted(p.name for p in unit_dir.iterdir()) == ["coderstanding-helper.service",
                                                          "coderstanding-tray.service"]
    assert ["systemctl", "--user", "daemon-reload"] in ran
    helper.uninstall()
    assert list(unit_dir.iterdir()) == []


def test_checkout_units_get_pythonpath(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))
    monkeypatch.setattr(helper.shutil, "which", lambda name: None)
    text = next(iter(helper.SystemdUser().files(False).values()))
    assert f'ExecStart="{tmp_path / "bin" / "python"}" "-m" "coderstanding"' in text
    assert f'Environment="PYTHONPATH={SCANNER}"' in text


# --- hook and skill -----------------------------------------------------------------------------

def test_hook_snippet_uses_the_command_not_a_repo_path():
    data = json.loads(hook.settings_snippet("coderstanding hook-run"))["hooks"]
    assert data["SessionStart"][0]["hooks"][0]["command"] == "coderstanding hook-run"
    assert data["PreToolUse"][0]["matcher"] == "Grep|Glob|Read|Bash"


def test_hook_command_falls_back_to_the_quoted_entry_point(fake_install, monkeypatch):
    monkeypatch.setattr(hook.shutil, "which", lambda name: None)
    assert hook.hook_command() == f'"{fake_install / "coderstanding"}" "hook-run"'
    monkeypatch.setattr(hook.shutil, "which", lambda name: "/x/coderstanding")
    assert hook.hook_command() == "coderstanding hook-run"


def test_hook_finds_windows_drive_paths(monkeypatch):
    monkeypatch.setattr(userconfig, "windows", lambda: True)
    assert hook.DRIVE_PATH.match("C:\\code\\app") and hook.DRIVE_PATH.match("d:/code")
    target = hook.bash_target("grep -r foo C:\\code\\app", Path("/cwd"))
    assert target is not None and str(target).endswith("C:\\code\\app")


def test_skill_install_asks_before_overwriting(tmp_path):
    dest = tmp_path / "skills" / "coderstanding"
    assert skill.install(dest) == dest
    assert (dest / "SKILL.md").is_file() and (dest / "AI_GUIDE.md").is_file()
    (dest / "SKILL.md").write_text("mine")
    assert skill.install(dest) is None  # no tty, no --yes: left alone
    assert (dest / "SKILL.md").read_text() == "mine"
    skill.install(dest, yes=True)
    assert (dest / "SKILL.md").read_text() != "mine"


def test_skill_install_replaces_a_link_without_writing_through_it(tmp_path):
    target = tmp_path / "checkout_skill"
    target.mkdir()
    (target / "SKILL.md").write_text("repo copy")
    dest = tmp_path / "skills" / "coderstanding"
    dest.parent.mkdir()
    dest.symlink_to(target)
    skill.install(dest, yes=True)
    assert not dest.is_symlink() and (target / "SKILL.md").read_text() == "repo copy"


# --- platform folders and Windows-style paths ----------------------------------------------------

def test_linux_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODERSTANDING_DATA_HOME", raising=False)
    assert userconfig.config_dir() == tmp_path / ".config" / "coderstanding"
    assert default_data_dir("a") == tmp_path / ".local" / "share" / "coderstanding" / "a"


def test_windows_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(userconfig, "windows", lambda: True)
    monkeypatch.delenv("CODERSTANDING_DATA_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert userconfig.config_dir() == tmp_path / "Roaming" / "coderstanding"
    assert default_data_dir("a") == tmp_path / "Local" / "coderstanding" / "data" / "a"
    assert userconfig.log_dir() == tmp_path / "Local" / "coderstanding"


def test_windows_scan_command_has_no_nice(tmp_path, monkeypatch):
    from coderstanding import hub
    app = type("A", (), {"root": tmp_path, "data": tmp_path})()
    monkeypatch.setattr(userconfig, "windows", lambda: True)
    assert "nice" not in hub.scan_command(app)
    monkeypatch.setattr(userconfig, "windows", lambda: False)
    assert hub.scan_command(app)[0] == "nice"


def test_internal_paths_stay_posix_for_windows_style_input():
    win = PureWindowsPath("C:\\code\\app\\src\\a.gd")
    assert win.relative_to(PureWindowsPath("C:\\code\\app")).as_posix() == "src/a.gd"
    assert normalize_ref_path("./util.gd", "src/a.gd", "C:\\code\\app") == "src/util.gd"
    assert res_to_rel("res://x/y.gd", "game") == "game/x/y.gd"
    assert rel_to_res("game/x/y.gd", "game") == "res://x/y.gd"


def test_registry_keeps_windows_repo_paths_as_written(tmp_path, monkeypatch):
    from coderstanding import apps
    monkeypatch.setenv(apps.REGISTRY_ENV, str(tmp_path / "apps.json"))
    apps.save_registry({"w": {"path": "C:\\Users\\me\\code\\app"}})
    assert apps.load_registry()["w"]["path"] == "C:\\Users\\me\\code\\app"
