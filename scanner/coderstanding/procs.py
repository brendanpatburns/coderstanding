"""Start child processes quietly: the one place that calls subprocess (tests/test_procs.py fails
on any other call). The Windows helper runs under pythonw (no console), so every console child
(git.exe, the rescan child, taskkill) would otherwise open its own visible window. Elsewhere
these are plain subprocess calls."""

import subprocess

from . import userconfig

CREATE_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW, which only exists on Windows


def quiet_kwargs(flags=0):
    """Popen/run options that keep a child from opening a console window (Windows); `flags` adds
    more creation flags (priority class, detached). Nothing elsewhere."""
    return {"creationflags": CREATE_NO_WINDOW | flags} if userconfig.windows() else {}


def run_quiet(command, flags=0, **options):
    return subprocess.run(command, **quiet_kwargs(flags), **options)


def popen_quiet(command, flags=0, **options):
    return subprocess.Popen(command, **quiet_kwargs(flags), **options)
