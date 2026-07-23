# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the QEMU sanitizer diagnostics parser (`f.qsu.diagnostics`)."""

from f.qsu import diagnostics

# The two lines the MDTS live fire produced in the host journal, verbatim.
UBSAN_8641 = (
    "../hw/nvme/ctrl.c:8641:32: runtime error: shift exponent 32 is too "
    "large for 32-bit type 'int'"
)
UBSAN_1699 = (
    "../hw/nvme/ctrl.c:1699:36: runtime error: shift exponent 32 is too "
    "large for 32-bit type 'unsigned int'"
)


def test_parses_a_ubsan_line_into_a_located_finding():
    (f,) = diagnostics.parse([UBSAN_1699])
    assert f["sanitizer"] == "ubsan"
    assert f["category"] == "shift"
    assert (f["file"], f["line"], f["col"]) == ("../hw/nvme/ctrl.c", 1699, 36)
    assert f["raw"] == UBSAN_1699


def test_parses_both_live_sites():
    findings = diagnostics.parse([UBSAN_8641, UBSAN_1699])
    assert [f["line"] for f in findings] == [8641, 1699]
    assert diagnostics.locations(findings) == [
        "../hw/nvme/ctrl.c:8641",
        "../hw/nvme/ctrl.c:1699",
    ]


def test_deduplicates_a_location_repeated_across_restarts():
    """Each process re-reports its once; the same site from two runs is one finding."""
    findings = diagnostics.parse([UBSAN_1699, UBSAN_1699, UBSAN_1699])
    assert len(findings) == 1


def test_ignores_non_sanitizer_lines():
    noise = ["Booting Linux", "systemd[1]: Reached target", "random qemu chatter"]
    assert diagnostics.parse(noise) == []


def test_parses_an_asan_error_header():
    line = "==1234==ERROR: AddressSanitizer: heap-use-after-free on address 0x60"
    (f,) = diagnostics.parse([line])
    assert f["sanitizer"] == "asan"
    assert f["category"] == "heap-use-after-free"


def test_parses_a_summary_location():
    line = "SUMMARY: AddressSanitizer: heap-use-after-free block/foo.c:42 in bar"
    (f,) = diagnostics.parse([line])
    assert f["sanitizer"] == "asan"
    assert (f["file"], f["line"]) == ("block/foo.c", 42)


def test_verdict_flags_diagnostics_regardless_of_selection():
    v = diagnostics.verdict(diagnostics.parse([UBSAN_1699]), "ubsan")
    assert v["status"] == "diagnostics"
    assert v["ok"] is False
    assert v["count"] == 1
    assert v["locations"] == ["../hw/nvme/ctrl.c:1699"]


def test_verdict_clean_for_a_quiet_sanitized_build():
    v = diagnostics.verdict([], "ubsan")
    assert v["status"] == "clean"
    assert v["ok"] is True


def test_verdict_not_sanitized_for_a_stock_build():
    v = diagnostics.verdict([], "none")
    assert v["status"] == "not_sanitized"
    assert v["ok"] is True


def test_verdict_empty_selection_reads_as_not_sanitized():
    assert diagnostics.verdict([], "")["status"] == "not_sanitized"


def test_store_name_reads_the_positional_sanitizer_segment():
    # The label is itself "ubsan-test", so "ubsan" appears twice; only the segment
    # immediately before the 12-hex identity is the sanitizer.
    name = "lxcf-qemu-11.0.0-ubsan-test-ubsan-274d18f9c692"
    assert diagnostics.sanitizer_from_store_name(name) == "ubsan"


def test_store_name_of_a_stock_ubsan_test_build_is_not_sanitized():
    name = "lxcf-qemu-11.0.0-ubsan-test-274d18f9c692"
    assert diagnostics.sanitizer_from_store_name(name) == "none"


def test_store_name_reads_the_combined_selection():
    name = "lxcf-qemu-11.0.0-vanilla-asan+ubsan-274d18f9c692"
    assert diagnostics.sanitizer_from_store_name(name) == "asan+ubsan"


def test_store_name_of_a_bare_stock_build_is_not_sanitized():
    assert diagnostics.sanitizer_from_store_name("lxcf-qemu-a2bea2e41e31") == "none"
