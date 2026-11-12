import pytest

from coderstanding.apps import DATA_HOME_ENV, REGISTRY_ENV, registry_path

# The user's real apps list, found before any test changes the environment.
REAL_REGISTRY = registry_path()


def registry_bytes():
    return REAL_REGISTRY.read_bytes() if REAL_REGISTRY.exists() else None


@pytest.fixture(autouse=True, scope="session")
def real_registry_untouched():
    """Fails the run if any test changed the user's real apps.json (leaked entries pointing at /tmp)."""
    before = registry_bytes()
    yield
    assert registry_bytes() == before, f"a test wrote to the real {REAL_REGISTRY}"


@pytest.fixture(autouse=True)
def temp_registry(tmp_path, monkeypatch):
    """Every test gets its own apps.json and default data folders; tests that need a named
    one set the same variables again."""
    monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "default-registry" / "apps.json"))
    monkeypatch.setenv(DATA_HOME_ENV, str(tmp_path / "default-data"))


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
