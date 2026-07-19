# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the kselftest flat-KTAP parser and shared verdict rule."""

from f.selftests.common import item_unit, parse_ktap, run_status, unit_escape

KTAP = """\
TAP version 13
1..4
ok 1 selftests: lib: printf.sh
not ok 2 selftests: lib: scanf.sh # exit=1
ok 3 selftests: lib: bitmap.sh # SKIP not configured
ok 4 selftests: lib: prime_numbers.sh # XFAIL known broken
# Totals: pass:1 fail:1 xfail:1 xpass:0 skip:1 error:0
"""


def test_parses_the_flat_document():
    s = parse_ktap(KTAP)
    assert s["report_present"] is True
    assert s["plan"] == 4
    assert (s["passed"], s["failed"], s["skipped"]) == (2, 1, 1)
    by_test = {t["test"]: t for t in s["tests"]}
    assert by_test["scanf.sh"]["status"] == "failed"
    assert by_test["scanf.sh"]["message"] == "exit=1"
    assert by_test["bitmap.sh"]["status"] == "notrun"
    assert by_test["prime_numbers.sh"]["status"] == "passed"
    assert by_test["prime_numbers.sh"]["message"].startswith("XFAIL")
    assert s["complete"] is True


def test_truncated_document_is_incomplete():
    cut = "\n".join(KTAP.splitlines()[:3])
    s = parse_ktap(cut)
    assert s["report_present"] is True
    assert s["complete"] is False


def test_disagreeing_totals_are_incomplete():
    lied = KTAP.replace("pass:1 fail:1", "pass:4 fail:0")
    assert parse_ktap(lied)["complete"] is False


def test_no_header_means_no_report():
    assert parse_ktap("random journal lines")["report_present"] is False


def test_run_status_never_passes_a_vacuous_or_errored_run():
    assert run_status([]) == "failed"
    assert run_status([{"status": "passed"}]) == "passed"
    assert run_status([{"status": "passed"}, {"status": "failed"}]) == "failed"
    assert run_status([{"status": "passed"}, {"error": {"name": "SSH"}}]) == "failed"


def test_unit_escape_follows_systemd_do_escape():
    assert unit_escape("net/forwarding") == "net-forwarding"
    assert unit_escape("cpu-hotplug") == r"cpu\x2dhotplug"
    assert unit_escape("timers:posix_timers.sh") == "timers:posix_timers.sh"
    assert unit_escape(".hidden") == r"\x2ehidden"
    assert unit_escape("a\\b") == r"a\x5cb"


def test_item_unit_picks_the_template_by_the_colon():
    assert item_unit("net/forwarding") == "kselftest@net-forwarding.service"
    assert item_unit("cpu-hotplug") == r"kselftest@cpu\x2dhotplug.service"
    assert (
        item_unit("timers:posix_timers") == "kselftest-test@timers:posix_timers.service"
    )
