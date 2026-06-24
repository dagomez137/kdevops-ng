# SPDX-License-Identifier: copyleft-next-0.3.1
"""Collect one xfstests section's results from the `fstests` share (read-only).

The guest's `./check -s <section> -R xunit` writes its report to
`RESULT_BASE/<section>/result.xml`. RESULT_BASE is `$PWD/results` with the unit's
`WorkingDirectory=.../%v`, so results are keyed by the guest's kernel release:
`<share>/<kver>/results/<section>`. This reads that one section's `result.xml`
and returns a summary (`passed`/`failed`/`skipped`, the failing test names + messages,
the notruns), plus the paths of any `.out.bad` diffs and the section `check.log`
xfstests left beside it. A still-running or crashed section (no/partial report) degrades
to zeros with a flag rather than failing. Read-only; the host never contacts the guest.

Equivalent command:

    cat "$WORKERS_DIR/shared/fstests/<vm>/<kver>/results/<section>/result.xml"
"""

from __future__ import annotations

from f.fstests.common import (
    list_vms as _list_vms,
    parse_xunit,
    read_xfs_info,
    section_config,
    section_results_dir,
)


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.fstests.common.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, section: str, kernel_version: str) -> dict:
    results_dir = section_results_dir(vm_name, kernel_version, section)
    print(f"+ reading {results_dir}", flush=True)
    summary = parse_xunit(results_dir, section=section)
    print(f"section {section}: passed={summary['passed']} failed={summary['failed']} "
          f"skipped={summary['skipped']} (report_present={summary['report_present']})", flush=True)
    # Scalar overview at the top level so the check flow's per-section forloop renders a
    # tidy one-row-per-section table (Windmill JSON-stringifies nested arrays into a cell).
    # The per-test/failure detail rides under `detail`, consumed only by `f/fstests/report`
    # to build the run's per-section tables; `status` folds "no report" into a failure.
    geometry = section_config(vm_name, section)
    # Merge the realized xfs_info (feature bits) captured by f/fstests/prepare, if present.
    xi = read_xfs_info(vm_name, section)
    if xi:
        geometry["features"] = xi["features"]
        geometry["xfs_info"] = xi["raw"]
    report_present = summary["report_present"]
    return {
        "section": section,
        "vm_name": vm_name,
        "kernel_version": kernel_version,
        "fstype": geometry.get("fstype", ""),
        "status": "passed" if (report_present and not summary["failed"]) else "failed",
        "report_present": report_present,
        "tests": summary["tests"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "notrun": summary["skipped"],
        "iterations": summary["iterations"],
        "detail": {
            "per_test": summary["per_test"],
            "failures": summary["failures"],
            "notruns": summary["notruns"],
            "out_bad": summary["out_bad"],
            "report": summary["report"],
            "check_log": summary["check_log"],
            "geometry": geometry,
            "error": summary.get("error"),
        },
    }
