import pytest


@pytest.fixture(autouse=True)
def cache_outside_fixtures(request, tmp_path, monkeypatch):
    """Scans in tests keep their cache in tmp_path, never inside tests/fixtures.
    test_cache.py checks the default cache location itself, so it is left alone."""
    if request.module.__name__ != "test_cache":
        monkeypatch.setenv("CODERSTANDING_CACHE_DIR", str(tmp_path / "scan-cache"))


@pytest.fixture(autouse=True)
def fixed_human_name(monkeypatch):
    """Viewer writes are stored under the human's name (notes.human_author): `you` in tests,
    unless a test clears CODERSTANDING_USER to check the git user.name lookup."""
    monkeypatch.setenv("CODERSTANDING_USER", "you")
