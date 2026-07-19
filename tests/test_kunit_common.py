# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the KUnit KTAP parser (`f.kunit.common.parse_ktap`)."""

from f.kunit.common import (
    parse_ktap,
)

KTAP = """\
KTAP version 1
1..1
  # Subtest: example
  1..2
  ok 1 example_simple_test
  not ok 2 example_failing_test
# example: pass:1 fail:1 skip:0 total:2
not ok 1 example
"""


def test_parses_the_anchored_suite():
    s = parse_ktap(KTAP, "example")
    assert s["report_present"] is True
    assert (s["passed"], s["failed"], s["skipped"]) == (1, 1, 0)
    assert s["plan"] == 2
    assert [t["status"] for t in s["tests"]] == ["passed", "failed"]


def test_missing_suite_degrades_to_empty():
    s = parse_ktap("journal noise, no subtest", "example")
    assert s["report_present"] is False
    assert (s["passed"], s["failed"]) == (0, 0)


def test_empty_text_degrades_to_empty():
    assert parse_ktap("", "example")["report_present"] is False


def test_truncated_document_is_incomplete():
    cut = "\n".join(KTAP.splitlines()[:5])
    s = parse_ktap(cut, "example")
    assert s["report_present"] is True
    assert s["complete"] is False


def test_last_header_wins_over_a_previous_run():
    stale = KTAP.replace("not ok 2", "ok 2")
    s = parse_ktap(stale + KTAP, "example")
    assert (s["passed"], s["failed"]) == (1, 1)
