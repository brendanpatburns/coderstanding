"""Manual refresh only (DESIGN 4): API requests and CLI reads never walk files or scan."""

import json
import threading
from urllib.request import urlopen

import pytest

from coderstanding import apps, cli, filewalk, scan
from coderstanding.apps import DATA_HOME_ENV, REGISTRY_ENV, find_app, register, rescan
from coderstanding.hub import Hub
from coderstanding.server import HOST, Api, make_server


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "apps.json"))
    monkeypatch.setenv(DATA_HOME_ENV, str(tmp_path / "share"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("class Aa:\n    pass\n")
    register("p", repo, tmp_path / "data")
    rescan(find_app("p"))
    return repo


@pytest.fixture
def walks(monkeypatch):
    """Counts file walks and scans from here on."""
    count = []
    for module, name in ((filewalk, "git_files"), (filewalk, "walked_files"), (scan, "scan_repo"), (apps, "scan_repo")):
        original = getattr(module, name)
        monkeypatch.setattr(module, name, lambda *a, _o=original, _n=name, **k: (count.append(_n), _o(*a, **k))[1])
    return count


def test_api_request_burst_and_cli_reads_walk_nothing(project, walks, capsys):
    (project / "b.py").write_text("class Bb:\n    pass\n")  # changed since the scan: still no rescan
    server = make_server(Hub(Api), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for path in ["/api/apps", "/api/meta", "/api/view", "/api/view?app=p", "/api/meta"] * 4:
            with urlopen(f"http://{HOST}:{server.server_port}{path}") as response:
                json.load(response)
    finally:
        server.shutdown()
        server.server_close()
    for _ in range(3):
        cli.main(["graph", "p"])
        cli.main(["node", "p", "Aa"])
    capsys.readouterr()
    assert walks == []


def test_unscanned_project_says_press_refresh(project, tmp_path):
    register("fresh", project, tmp_path / "fresh-data")
    with pytest.raises(apps.AppError, match="press Refresh"):
        apps.load_graph(find_app("fresh"))


def test_cli_note_for_an_older_scanner_and_listing_flag(project, capsys):
    path = find_app("p").graph_path
    data = json.loads(path.read_text())
    data["scannerVersion"] = 1
    path.write_text(json.dumps(data))
    cli.main(["graph", "p"])
    assert "made by an older scanner" in capsys.readouterr().err
    assert Hub(Api).listing()["apps"][0]["staleScanner"] is True


def test_add_scans_unless_no_scan_or_already_registered(tmp_path, monkeypatch):
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "apps.json"))
    monkeypatch.setenv(DATA_HOME_ENV, str(tmp_path / "share"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("class Aa:\n    pass\n")
    cli.main(["add", "skipped", str(repo), "--no-scan"])
    assert not find_app("skipped").graph_path.exists()
    cli.main(["add", "scanned", str(repo)])
    assert find_app("scanned").graph_path.exists()
    find_app("scanned").graph_path.unlink()
    cli.main(["add", "scanned", str(repo)])  # re-pointing: no rescan
    assert not find_app("scanned").graph_path.exists()
    cli.main(["add", "scanned", str(repo), "--rescan"])
    assert find_app("scanned").graph_path.exists()
