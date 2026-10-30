"""The real `scan-pipeline` tour of this repo (.coderstanding/tours/) against a fresh scan:
it fails when a step's code is gone (lost) or changed since the tour was written (stale), so
the tour stays true as the code changes. After a deliberate change, re-check the step's text
and marks, then `coderstanding tour coderstanding scan-pipeline --still-true`."""

from pathlib import Path

import pytest

from coderstanding.fingerprints import SourceLines
from coderstanding.languages.python import PythonModule
from coderstanding.notes import Notes
from coderstanding.scan import scan_repo
from coderstanding.tours import Tours

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tour():
    graph = scan_repo(REPO, modules=[PythonModule()], use_cache=False)
    tours = Tours(REPO / ".coderstanding", graph, Notes(), SourceLines(REPO))
    return tours.tour_json(tours.get("scan-pipeline"))


def test_no_step_is_lost_or_stale(tour):
    assert len(tour["steps"]) == 8
    bad = [(n, s["title"], "lost" if s["lost"] else "changed", s["lost_targets"])
           for n, s in enumerate(tour["steps"], 1) if s["lost"] or s["stale"]]
    assert not bad, f"scan-pipeline tour steps to re-check: {bad}"


def test_every_step_shows_and_marks_code(tour):
    for step in tour["steps"]:
        assert step["code"]["mark"] and step["code"]["lines"], step["title"]
