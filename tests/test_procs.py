"""Child processes start quietly (no console window under pythonw), and nothing else calls subprocess."""

import re
from pathlib import Path

from coderstanding import procs, userconfig

SCANNER = Path(__file__).resolve().parents[1] / "scanner" / "coderstanding"
CALL = re.compile(r"\bsubprocess\.(run|Popen|check_output|check_call|call|getoutput|getstatusoutput)\b|\bos\.(system|popen)\b")


def test_quiet_kwargs_hide_the_window_on_windows(monkeypatch):
    monkeypatch.setattr(userconfig, "windows", lambda: True)
    assert procs.quiet_kwargs() == {"creationflags": procs.CREATE_NO_WINDOW}
    assert procs.quiet_kwargs(0x4000)["creationflags"] == procs.CREATE_NO_WINDOW | 0x4000


def test_quiet_kwargs_are_empty_elsewhere(monkeypatch):
    monkeypatch.setattr(userconfig, "windows", lambda: False)
    assert procs.quiet_kwargs(0x4000) == {}


def test_run_and_popen_pass_the_quiet_options(monkeypatch):
    seen = []
    monkeypatch.setattr(userconfig, "windows", lambda: True)
    monkeypatch.setattr(procs.subprocess, "run", lambda cmd, **kw: seen.append(("run", kw)))
    monkeypatch.setattr(procs.subprocess, "Popen", lambda cmd, **kw: seen.append(("popen", kw)))
    procs.run_quiet(["x"], capture_output=True)
    procs.popen_quiet(["x"], flags=8)
    assert seen[0] == ("run", {"creationflags": procs.CREATE_NO_WINDOW, "capture_output": True})
    assert seen[1][1]["creationflags"] == procs.CREATE_NO_WINDOW | 8


def test_every_subprocess_call_goes_through_procs():
    offenders = [f"{path.name}:{n}: {line.strip()}"
                 for path in sorted(SCANNER.rglob("*.py")) if path.name != "procs.py"
                 for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                 if CALL.search(line)]
    assert offenders == []
