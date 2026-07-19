# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the xfstests xunit parser and shared verdict rule."""

from f.fstests.common import parse_xunit, run_status

SINGLE_PASS = """\
<testsuite name="xfs_4k" tests="3" failures="1" skipped="1">
  <testcase classname="xfstests" name="generic/001" time="2.1"/>
  <testcase classname="xfstests" name="generic/002" time="0.4">
    <failure message="output mismatch" type="TestFail"/>
  </testcase>
  <testcase classname="xfstests" name="generic/003" time="0.0">
    <skipped message="not supported"/>
  </testcase>
</testsuite>
"""

# Two `-i` passes of one test: the final pass passed, so the header counters
# claim zero failures while the body still carries the first pass's failure.
TWO_PASS = """\
<testsuite name="xfs_4k" tests="1" failures="0" skipped="0">
  <testcase classname="xfstests" name="generic/010" time="1.0">
    <failure message="flaked on pass 1" type="TestFail"/>
  </testcase>
  <testcase classname="xfstests" name="generic/010" time="1.0"/>
</testsuite>
"""


def test_missing_report_degrades_with_the_full_shape(tmp_path):
    s = parse_xunit(tmp_path / "xfs_4k", section="xfs_4k")
    assert s["report_present"] is False
    assert s["error"] == "no xunit report"
    assert (s["passed"], s["failed"], s["skipped"], s["tests"]) == (0, 0, 0, 0)
    assert s["iterations"] == 0
    assert s["per_test"] == []


def test_unparseable_report_degrades(tmp_path):
    d = tmp_path / "xfs_4k"
    d.mkdir()
    (d / "result.xml").write_text("<testsuite")
    s = parse_xunit(d, section="xfs_4k")
    assert s["report_present"] is True
    assert s["error"].startswith("unparseable xunit report")
    assert s["passed"] == 0


def test_single_pass_counts_and_failure_first_ordering(tmp_path):
    d = tmp_path / "xfs_4k"
    d.mkdir()
    (d / "result.xml").write_text(SINGLE_PASS)
    s = parse_xunit(d, section="xfs_4k")
    assert (s["passed"], s["failed"], s["skipped"], s["tests"]) == (1, 1, 1, 3)
    assert s["iterations"] == 1
    assert [t["test"] for t in s["per_test"]] == [
        "generic/002",
        "generic/003",
        "generic/001",
    ]
    assert s["failures"][0]["message"] == "output mismatch"
    assert s["notruns"] == ["generic/003"]


def test_multi_pass_run_derives_from_the_body_not_the_header(tmp_path):
    d = tmp_path / "xfs_4k"
    d.mkdir()
    (d / "result.xml").write_text(TWO_PASS)
    s = parse_xunit(d, section="xfs_4k")
    assert s["failed"] == 1
    assert s["iterations"] == 2
    row = s["per_test"][0]
    assert (row["status"], row["runs"], row["fails"]) == ("failed", 2, 1)


def test_run_status_never_passes_a_vacuous_run():
    assert run_status([]) == "failed"
    assert run_status([{"status": "passed"}]) == "passed"
    assert run_status([{"status": "passed"}, {"status": "failed"}]) == "failed"
    assert run_status([{"error": {"name": "SSH"}}]) == "failed"
