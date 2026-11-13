"""find: fuzzy words, names + notes + linked text, no-hit help, --tests."""

import pytest

from coderstanding.cli import main
from coderstanding.wordmatch import close_names, one_typo, split_words, text_quality, EXACT, PART, TYPO
from helpers import write_files

FILES = {
    "crash_reports.py": '"""Server side."""\n\nMAX_PER_HOUR = 5\n\n\nclass CrashReporter:\n'
                        '    def save_report(self, text):\n        return text\n',
    "lobby.py": "from crash_reports import CrashReporter\n\n\nclass Lobby:\n    def __init__(self):\n"
                "        self.reporter = CrashReporter()\n\n    def start(self):\n"
                '        self.reporter.save_report("a")\n',
    "tests/test_crash.py": "from crash_reports import CrashReporter\n\n\n"
                           'def test_save():\n    CrashReporter().save_report("x")\n',
}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODERSTANDING_REGISTRY", str(tmp_path / "apps.json"))
    monkeypatch.setenv("CODERSTANDING_DATA_HOME", str(tmp_path / "data"))
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    main(["add", "t", str(repo)])
    return repo


def run(capsys, *argv):
    capsys.readouterr()
    main(list(argv))
    return capsys.readouterr().out.splitlines()


def test_split_words_handles_every_case_style():
    assert split_words("MAX_PER_HOUR") == ["max", "per", "hour"]
    assert split_words("CrashReporter") == ["crash", "reporter"]
    assert split_words("crash_reports.gd") == ["crash", "reports", "gd"]
    assert split_words("HTTPServer2") == ["http", "server", "2"]


def test_word_quality_tiers():
    assert text_quality("report", "crash_reports") == EXACT  # stem
    assert text_quality("rep", "reports") == PART
    assert text_quality("reprot", "report") == TYPO
    assert text_quality("zebra", "reports") == 0
    assert one_typo("hour", "hoar") and not one_typo("hour", "hoarse")


def test_close_names_suggests_by_word_and_whole():
    assert "CrashReporter" in close_names(["crashreporte"], ["CrashReporter", "Lobby"])
    assert close_names(["zzzz"], ["Lobby"]) == []


def test_words_match_across_class_and_field_names(app, capsys):
    lines = run(capsys, "find", "t", "lobby", "reporter")  # class word + field word
    assert any(l.startswith("Lobby#reporter  field") for l in lines)
    assert any(l.startswith("Lobby  ") for l in lines)  # the class itself, via its field


def test_notes_and_typos_match_and_whole_name_ranks_first(app, capsys):
    lines = run(capsys, "find", "t", "crashreporter")
    assert lines[1].startswith("CrashReporter ")  # whole name beats the file and its functions
    assert any("save_report" in l for l in run(capsys, "find", "t", "saving", "report"))
    assert len(run(capsys, "find", "t", "reprot"))> 1  # one typo still finds


def test_no_hit_says_so_and_suggests(app, capsys):
    out = run(capsys, "find", "t", "qqqq", "zzz")
    assert "(names + notes, not file text)" in out[0] and out[-1] == "for literal text: find t --text <pattern>"
    near = run(capsys, "find", "t", "lobbxx", "zzz")  # a word with a close name, but no match
    assert any(l.startswith("close names:") for l in near)


def test_tests_flag_lists_callers_and_nearest_tests(app, capsys):
    lines = run(capsys, "find", "t", "save_report", "--tests")
    at = next(i for i, l in enumerate(lines) if l.startswith("CrashReporter#save_report"))
    assert lines[at + 1] == "  callers: Lobby#start"
    assert lines[at + 2] == "  tests: tests/test_crash.py"
    plain = run(capsys, "find", "t", "save_report")
    assert not any("callers:" in l for l in plain)


def test_linked_spec_text_is_searched(app, capsys):
    main(["spec", "t", "--add", "Rate limit", "--links", "CrashReporter",
          "--text", "throttle uploads"])
    lines = run(capsys, "find", "t", "throttle")
    assert any(l.startswith("CrashReporter ") and "in: Rate limit" in l for l in lines)


def test_lost_notes_stay_silent_in_find(app, capsys):
    run(capsys, "find", "t", "lobby")
    assert "lost" not in capsys.readouterr().err


def test_multi_word_find_ranks_by_words_matched(app, capsys):
    lines = run(capsys, "find", "t", "lobby", "reporter", "zebra")  # "zebra" matches nothing
    assert lines[1].startswith("Lobby")  # both real words match: ranked above a one-word hit
    assert any(l.startswith("CrashReporter") for l in lines)  # one word is enough
    assert [l for l in lines if l.startswith("CrashReporter")] != []
    order = [l.split()[0] for l in lines[1:] if l.split()]
    assert order.index("Lobby") < order.index("CrashReporter")


def test_find_with_no_word_matching_still_has_no_hits(app, capsys):
    lines = run(capsys, "find", "t", "zebra", "giraffe")
    assert not any(l.startswith(("Lobby", "CrashReporter")) for l in lines)
