"""The hosted page origin comes from the user's config, never from the code (userconfig.py)."""

from coderstanding import userconfig


def test_origin_from_env_then_hosted_json(tmp_path, monkeypatch):
    monkeypatch.setattr(userconfig, "config_dir", lambda: tmp_path)
    monkeypatch.delenv(userconfig.ALLOW_ORIGIN_ENV, raising=False)
    assert userconfig.hosted_origin() is None  # no file: no cross-origin page
    (tmp_path / "hosted.json").write_text('{"origin": "https://code.example.com"}')
    assert userconfig.hosted_origin() == "https://code.example.com"
    monkeypatch.setenv(userconfig.ALLOW_ORIGIN_ENV, "https://other.example.com")
    assert userconfig.hosted_origin() == "https://other.example.com"


def test_broken_hosted_json_means_none(tmp_path, monkeypatch):
    monkeypatch.setattr(userconfig, "config_dir", lambda: tmp_path)
    monkeypatch.delenv(userconfig.ALLOW_ORIGIN_ENV, raising=False)
    (tmp_path / "hosted.json").write_text("not json")
    assert userconfig.hosted_origin() is None
    (tmp_path / "hosted.json").write_text("[1]")
    assert userconfig.hosted_origin() is None
