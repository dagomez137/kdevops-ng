# SPDX-License-Identifier: copyleft-next-0.3.1
"""Aggregate the per-suite KUnit results into one run verdict.

Folds the list of `f/kunit/collect` results (one per suite, collected by the flow's
per-suite forloop) into a single rollup: the per-suite summaries, the per-test rows,
and a `status` of `passed` only when every suite passed (a failed, crashed, or
notrun suite fails the run, and so does an empty suite list: aggregating nothing
must never read as a pass). Pure aggregation, no guest contact. `f/kunit/judge`
turns the same per-suite results into the job verdict (`run_status` is the one
shared rule). When a `vm_name` is given it also writes the full rollup to
`<share>/<kver>/report.json` (atomically), so the run's verdict is recoverable
from the share alone.

The returned value is a Windmill `render_all` display of three NATIVE tables (no
markdown: arrays of objects render as real sortable/searchable tables;
`render_all` must stay the sole key or the display falls back to raw JSON):

  1. run info: testsuite, kernel, guest
  2. per suite: suite, tests, passed, failed, notrun, status
  3. per test: one row per test across all suites (failures first)

Equivalent command:

    cat "$WORKERS_DIR/shared/kunit/<vm>/<kver>/report.json"   # when vm_name is given
"""

from __future__ import annotations

import json

from f.kunit.common import _atomic_write, run_status, share_dir

_ICON = {"passed": "✅", "failed": "❌", "notrun": "⊘"}
# Failures first in the per-test table, then notruns, then passes.
_RANK = {"failed": 0, "notrun": 1, "passed": 2}


def _per_test_rows(suite: str, per_test: list[dict]) -> list[dict]:
    """One display row per test of a suite: status icon, the failing/skip message."""
    return [
        {
            "suite": suite,
            "test": t.get("name"),
            "status": _ICON.get(t.get("status", ""), t.get("status", "")),
            "message": t.get("message", ""),
        }
        for t in per_test
    ]


def main(per_suite: list[dict] | None = None, vm_name: str = "") -> dict:
    suites = list(per_suite or [])
    # One shared rule with f/kunit/judge: collect already folds crashes,
    # timeouts, truncated KTAP and did-nothing runs into failed/notrun statuses.
    status = run_status(suites)
    kernel_version = next(
        (s.get("kernel_version") for s in suites if s.get("kernel_version")), ""
    )

    # Table 2: one row per suite: its counts and verdict icon. The forloop runs
    # with skip_failures, so a suite whose step errored hard (an SSH failure in
    # start) arrives as an error object, not a collect result; render it as a
    # failed row rather than a blank one (run_status already fails the run).
    suite_rows = [
        {
            "suite": s.get("suite") or "(step failed)",
            "tests": int(s.get("tests", 0) or 0),
            "passed": int(s.get("passed", 0) or 0),
            "failed": int(s.get("failed", 0) or 0),
            "notrun": int(s.get("notrun", 0) or 0),
            "status": _ICON.get(s.get("status", ""), s.get("status", "") or "❌"),
        }
        for s in suites
    ]

    # Table 3: one row per test across all suites, failures first.
    test_rows = [
        r
        for s in suites
        for r in _per_test_rows(
            s.get("suite"), (s.get("detail") or {}).get("per_test") or []
        )
    ]
    test_rows.sort(
        key=lambda r: (
            _RANK.get(r["status"], 3),
            r.get("suite") or "",
            r.get("test") or "",
        )
    )

    failures = [
        {
            "suite": s.get("suite"),
            "test": f.get("name"),
            "message": f.get("message", ""),
        }
        for s in suites
        for f in ((s.get("detail") or {}).get("failures") or [])
    ]
    rollup = {
        "status": status,
        "kernel_version": kernel_version,
        "suites": suites,
        "failures": failures,
    }
    print(
        f"status={status} suites={len(suites)} "
        f"tests={sum(r['tests'] for r in suite_rows)} failing={len(failures)}",
        flush=True,
    )

    if vm_name:
        # Key the aggregate by kernel too (results are kver-keyed), so two kernels' runs
        # on one guest don't clobber each other's report.json; fall back to the share
        # root when the kernel is unknown (degraded run).
        out_dir = (
            share_dir(vm_name) / kernel_version
            if kernel_version
            else share_dir(vm_name)
        )
        path = out_dir / "report.json"
        _atomic_write(path, json.dumps(rollup, indent=2) + "\n")
        print(f"+ wrote {path}", flush=True)
        rollup["report_json"] = str(path)

    run_info = [
        {
            "testsuite": "kunit",
            "kernel": kernel_version or "?",
            "guest": vm_name or "?",
        }
    ]
    return {"render_all": [run_info, suite_rows, test_rows]}
