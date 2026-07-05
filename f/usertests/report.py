# SPDX-License-Identifier: copyleft-next-0.3.1
"""Aggregate the per-harness usertests results into one run verdict.

Folds the list of `f/usertests/collect` results (one per run item, collected
by the flow's per-item forloop) into a single rollup: the per-harness
summaries, the per-check rows, and a `status` of `passed` only when every item
passed (a failed or crashed item fails the run, and so does an empty item
list: aggregating nothing must never read as a pass). Pure aggregation, no
guest contact. `f/usertests/judge` turns the same per-item results into the
job verdict (`run_status` is the one shared rule). When a `vm_name` is given
it also writes the full rollup to `<share>/<kver>/report.json` (atomically),
so the run's verdict is recoverable from the share alone.

The returned value is a Windmill `render_all` display of three NATIVE tables
(no markdown: arrays of objects render as real sortable/searchable tables;
`render_all` must stay the sole key or the display falls back to raw JSON):

  1. run info: testsuite, kernel, guest, harnesses, time(s)
  2. per harness: harness, tests, passed, failed, time(s), status (the counts
     stay 0 for the silent and bench entries: their verdict is the exit code
     plus the global scans, not printed counts)
  3. per check: one row per finding across all items, failures first; a
     passing radix-tree/main row carries its captured `random seed` so the
     run is reproducible with `-s <seed>`

Equivalent command:

    cat "$WORKERS_DIR/shared/usertests/<vm>/<kver>/report.json"   # when vm_name is given
"""

from __future__ import annotations

import json

from f.usertests.common import _atomic_write, run_status, share_dir

_ICON = {"passed": "✅", "failed": "❌"}
# Failures first in the per-check table, then passes.
_RANK = {"failed": 0, "passed": 1}


def _time_s(runtime: float | None) -> float | int:
    """The `time(s)` column: a NUMBER so the table sorts, 0 only when unknown."""
    return round(float(runtime), 2) if runtime is not None else 0


def _per_check_rows(per_test: list[dict]) -> list[dict]:
    """One display row per check of an item; `status` stays raw here so the
    failures-first sort can rank it, and is iconized after sorting."""
    return [
        {
            "harness": t.get("harness", ""),
            "check": t.get("check", ""),
            "status": t.get("status", ""),
            "message": t.get("message", ""),
        }
        for t in per_test
    ]


def main(per_item: list[dict] | None = None, vm_name: str = "") -> dict:
    items = list(per_item or [])
    # One shared rule with f/usertests/judge: collect already folds crashes,
    # timeouts, aborts and truncated output into failed statuses.
    status = run_status(items)
    kernel_version = next(
        (s.get("kernel_version") for s in items if s.get("kernel_version")), ""
    )

    # Table 2: one row per run item: its counts and verdict icon. The forloop
    # runs with skip_failures, so an item whose step errored hard (an SSH failure
    # in start) arrives as an error object, not a collect result; render it as a
    # failed row rather than a blank one (run_status already fails the run).
    item_rows = [
        {
            "harness": s.get("item") or "(step failed)",
            "tests": int(s.get("tests", 0) or 0),
            "passed": int(s.get("passed", 0) or 0),
            "failed": int(s.get("failed", 0) or 0),
            "time(s)": _time_s(s.get("runtime")),
            "status": _ICON.get(s.get("status", ""), s.get("status", "") or "❌"),
        }
        for s in items
    ]

    # Table 3: one row per check across all items, failures first.
    check_rows = [
        r
        for s in items
        for r in _per_check_rows((s.get("detail") or {}).get("per_test") or [])
    ]
    check_rows.sort(
        key=lambda r: (
            _RANK.get(r["status"], 0),
            r.get("harness") or "",
            r.get("check") or "",
        )
    )
    for r in check_rows:
        r["status"] = _ICON.get(r["status"], r["status"] or "❌")

    failures = [
        {
            "harness": f.get("harness", ""),
            "check": f.get("check", ""),
            "message": f.get("message", ""),
        }
        for s in items
        for f in ((s.get("detail") or {}).get("per_test") or [])
        if f.get("status") == "failed"
    ]
    rollup = {
        "status": status,
        "kernel_version": kernel_version,
        "items": items,
        "failures": failures,
    }
    print(
        f"status={status} items={len(items)} "
        f"tests={sum(r['tests'] for r in item_rows)} failing={len(failures)}",
        flush=True,
    )

    if vm_name:
        # Key the aggregate by kernel too (results are kver-keyed), so two kernels'
        # runs on one guest don't clobber each other's report.json; fall back to
        # the share root when the kernel is unknown (degraded run).
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
            "testsuite": "usertests",
            "kernel": kernel_version or "?",
            "guest": vm_name or "?",
            "harnesses": len(items),
            "time(s)": round(sum(s.get("runtime") or 0 for s in items), 2),
        }
    ]
    return {"render_all": [run_info, item_rows, check_rows]}
