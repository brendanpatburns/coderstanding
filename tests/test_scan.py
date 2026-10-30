import json

import pytest

from coderstanding import cli
from coderstanding.language import LanguageModule, Registry
from fake_lang import FAKE, FakeModule, registry
from helpers import write_files


def test_fake_module_is_registered():
    assert registry.modules() == [FAKE] and isinstance(FAKE, FakeModule)


def test_registry_rejects_incomplete_or_duplicate_modules():
    reg = Registry()
    with pytest.raises(ValueError):
        reg.register(type("NoName", (LanguageModule,), {"patterns": ("*.x",)}))
    reg.register(FakeModule)
    with pytest.raises(ValueError):
        reg.register(FakeModule)


def test_cli_scan_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "scan_repo", lambda root, **kw: real_scan(root, modules=[FAKE], **kw))
    write_files(tmp_path, {
        "main.fk": "type Main class entry\nref composition World field\n",
        "world.fk": "type World class\nbase Node\nref association Main call ping\n",
        "broken.fk": "type Broken class\nboom\n",
    })
    cli.main(["scan", str(tmp_path)])
    data = json.loads((tmp_path / ".coderstanding" / "graph.json").read_text())
    assert data["repo"] == tmp_path.name and data["scannedAt"].endswith("Z")
    assert {n["id"]: n["level"] for n in data["nodes"]} == {"Main": 1, "World": 2}
    assert [(e["from"], e["to"], e["kind"]) for e in data["edges"]] == [
        ("Main", "World", "composition"), ("World", "Main", "association")]
    assert data["edges"][1]["calls"][0]["member"] == "ping"
    out = capsys.readouterr()
    assert "2 nodes, 2 edges" in out.out and "broken.fk" in out.err


def test_cli_output_option(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "scan_repo", lambda root, **kw: real_scan(root, modules=[FAKE], **kw))
    write_files(tmp_path, {"a.fk": "type A class\n"})
    out = tmp_path / "elsewhere.json"
    cli.main(["scan", str(tmp_path), "-o", str(out)])
    assert json.loads(out.read_text())["nodes"][0]["id"] == "A"


from coderstanding.scan import scan_repo as real_scan  # noqa: E402
