"""`coderstanding guide`: the AI guide, the snippet and --write."""

from coderstanding import guide
from coderstanding.cli import main


def test_guide_prints_the_packaged_guide(capsys):
    main(["guide"])
    assert capsys.readouterr().out == guide.guide_text()
    assert "guide for AI coding agents" in guide.guide_text()


def test_snippet_substitutes_the_app(capsys):
    main(["guide", "--snippet", "--app", "shop"])
    out = capsys.readouterr().out
    assert "coderstanding graph shop 1" in out and "<app>" not in out and "offline" in out
    main(["guide", "--snippet"])
    assert "coderstanding graph <app> 1" in capsys.readouterr().out


def test_write_creates_appends_replaces_and_is_idempotent(tmp_path, capsys):
    target = tmp_path / ".github" / "copilot-instructions.md"
    main(["guide", "--write", str(target), "--app", "one"])
    assert "created" in capsys.readouterr().out
    first = target.read_text()
    assert first.count(guide.BEGIN) == 1 and "graph one 1" in first
    main(["guide", "--write", str(target), "--app", "one"])
    assert target.read_text() == first
    main(["guide", "--write", str(target), "--app", "two"])
    text = target.read_text()
    assert text.count(guide.BEGIN) == 1 and "graph two 1" in text and "graph one 1" not in text

    other = tmp_path / "AGENTS.md"
    other.write_text("# Mine\nkeep this\n")
    main(["guide", "--write", str(other), "--app", "x"])
    main(["guide", "--write", str(other), "--app", "y"])
    text = other.read_text()
    assert text.startswith("# Mine\nkeep this\n") and text.count(guide.BEGIN) == 1 and "graph y 1" in text


def test_cursor_rule_gets_front_matter(tmp_path):
    rule = tmp_path / ".cursor" / "rules" / "coderstanding.mdc"
    guide.write_snippet(rule, "a")
    assert rule.read_text().startswith("---\n") and "alwaysApply: true" in rule.read_text()
