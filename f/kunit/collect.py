# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fold one KUnit suite's run-scoped KTAP into a per-test verdict (pure parse).

Parses the KTAP `f/kunit/wait` captured from the unit's journal, bounded by
start's cursor so it can only be this run's output, and never contacts the
guest (so a crashed guest still gets an honest verdict). The verdict is the
KTAP gated by the run's plumbing: `passed` needs the start job to have finished
(`result=done`, which already proves every strict `ExecStart` exited 0: a
successful process exit is journal-logged only at debug level, so
`exec_status` is empty on a clean run and populated only on failures), no
crash, no timeout, the suite's `# Subtest` block present with its `1..N` plan
matching the parsed result lines (a journal truncated mid-suite can never
pass), and no `not ok` line. A suite whose parsed run passed nothing at all (an empty body, or every
test skipped) is `notrun`, never a silent pass; anything else is `failed`.
Returns a scalar-topped dict so the run flow's per-suite forloop renders a tidy
one-row-per-suite table; the per-test detail and the raw KTAP ride under
`detail`, consumed by `f/kunit/report`.
"""

from __future__ import annotations

from f.common.remote import list_vms as _list_vms
from f.kunit.common import parse_ktap


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(
    vm_name: str,
    suite: str,
    kernel_version: str,
    ktap: str = "",
    result: str = "",
    exec_status: str = "",
    crashed: bool = False,
    timed_out: bool = False,
    runtime: float | None = None,
) -> dict:
    summary = parse_ktap(ktap or "", suite)

    # A successful process exit is journal-logged only at debug level, so
    # exec_status is EMPTY on a clean run; the "done" job record already proves
    # every strict ExecStart exited 0. A populated exec_status is failure detail.
    plumbing_ok = (
        result == "done"
        and str(exec_status) in ("", "0")
        and not crashed
        and not timed_out
    )
    if not plumbing_ok or not summary["complete"] or summary["failed"]:
        status = "failed"
    elif summary["passed"] == 0:
        status = "notrun"
    else:
        status = "passed"
    print(
        f"suite {suite}: status={status} result={result!r} "
        f"exec_status={exec_status!r} crashed={crashed} timed_out={timed_out} "
        f"passed={summary['passed']} failed={summary['failed']} "
        f"notrun={summary['skipped']} plan={summary['plan']} "
        f"complete={summary['complete']}",
        flush=True,
    )
    return {
        "suite": suite,
        "vm_name": vm_name,
        "kernel_version": kernel_version,
        "status": status,
        "result": result,
        "exec_status": exec_status,
        "crashed": crashed,
        "timed_out": timed_out,
        "runtime": runtime,
        "report_present": summary["report_present"],
        "tests": len(summary["tests"]),
        "passed": summary["passed"],
        "failed": summary["failed"],
        "notrun": summary["skipped"],
        "detail": {
            "per_test": summary["tests"],
            "failures": [t for t in summary["tests"] if t["status"] == "failed"],
            "ktap": ktap,
        },
    }
