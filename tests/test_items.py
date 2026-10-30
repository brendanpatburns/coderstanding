"""Markdown item store (items.py): front matter, sections, links, files."""

import pytest

from coderstanding.items import (Item, ItemError, ItemStore, dump_front_matter, join_sections,
                                 parse_front_matter, plain_links, split_sections)

TEXT = """---
id: scan-pipeline
title: How a scan works  # a comment
by: you
steps:
  - targets: [cli#run_scan, cli>scan]
    code: {at: cli#run_scan, lines: +6..+7, mark: [+6, +7], fp: 3f9a2c1b}
  - targets: [scan>language, PythonModule#parse]
    by: claude
    ai: true
    audio:
      rec: {by: you, fp: "0012", dur: 14.5}
---
## Where a scan starts
Everything begins with [[cli#run_scan]].

## A language module reads one file
Text two, see [[tour:other]].
"""


def test_parse_front_matter_subset():
    meta, body = parse_front_matter(TEXT)
    assert meta["title"] == "How a scan works"
    first, second = meta["steps"]
    assert first == {"targets": ["cli#run_scan", "cli>scan"],
                     "code": {"at": "cli#run_scan", "lines": "+6..+7", "mark": ["+6", "+7"],
                              "fp": "3f9a2c1b"}}
    assert second["ai"] is True and second["audio"]["rec"] == {"by": "you", "fp": "0012", "dur": 14.5}
    assert [t for t, _ in split_sections(body)] == ["Where a scan starts",
                                                    "A language module reads one file"]


def test_dump_then_parse_round_trips_awkward_values():
    meta = {"id": "x", "title": "Parse: then, resolve # really", "n": "0123", "flag": "true",
            "steps": [{"targets": ["A>B", "C#f"], "code": {"at": "C#f", "mark": ["+1"]}},
                      {"targets": [], "ai": True, "note": None}]}
    assert parse_front_matter(dump_front_matter(meta) + "\nbody")[0] == meta


def test_sections_round_trip_and_links():
    sections = [("One", "First [[A#f]] text."), ("Two", "Second.")]
    assert split_sections(join_sections(sections)) == sections
    assert plain_links("see [[A#f]] and [[tour:x]]", "`{}`") == "see `A#f` and `x`"


def test_missing_front_matter_is_an_error():
    with pytest.raises(ItemError):
        parse_front_matter("no fences")


def test_store_write_read_list_search_delete(tmp_path):
    store = ItemStore(tmp_path, "tours")
    store.write(Item("b-tour", {"title": "Bee"}, "## S\nfind the needle"))
    store.write(Item("a-tour", {"title": "Ant"}, "## S\nnothing"))
    assert store.ids() == ["a-tour", "b-tour"]
    assert store.read("b-tour").meta == {"id": "b-tour", "title": "Bee"}
    assert [i.id for i in store.search(["NEEDLE"])] == ["b-tour"]
    assert [i.id for i in store.search(["ant"])] == ["a-tour"]
    store.delete("a-tour")
    assert store.ids() == ["b-tour"]
    with pytest.raises(KeyError):
        store.read("a-tour")
    with pytest.raises(ItemError):
        store.path("../evil")
