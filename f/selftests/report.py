# SPDX-License-Identifier: copyleft-next-0.3.1
"""Aggregate the per-item kselftest results into one run verdict.

Folds the list of `f/selftests/collect` results (one per run item, collected by
the flow's per-item forloop) into a single rollup: the per-item summaries, the
per-test rows, and a `status` of `passed` only when every item passed (a
failed, crashed, or notrun item fails the run, and so does an empty item list:
aggregating nothing must never read as a pass). Pure aggregation, no guest
contact. `f/selftests/judge` turns the same per-item results into the job
verdict (`run_status` is the one shared rule). When a `vm_name` is given it
also writes the full rollup to `<share>/<kver>/report.json` (atomically), so
the run's verdict is recoverable from the share alone.

The returned value is a Windmill `render_all` display of three NATIVE tables (no
markdown: arrays of objects render as real sortable/searchable tables;
`render_all` must stay the sole key or the display falls back to raw JSON):

  1. run info: testsuite, kernel, guest, collections, time(s)
  2. per item: collection, tests, passed, failed, skipped, time(s), status
  3. per test: one row per test across all items (failures first)

Equivalent command:

    cat "$WORKERS_DIR/shared/selftests/<vm>/<kver>/report.json"   # when vm_name is given
"""

from __future__ import annotations

import json

from f.selftests.common import _atomic_write, run_status, share_dir

_ICON = {"passed": "✅", "failed": "❌", "notrun": "⊘"}
# Failures first in the per-test table, then notruns, then passes.
_RANK = {"failed": 0, "notrun": 1, "passed": 2}


def _time_s(runtime: float | None) -> float | int:
    """The `time(s)` column: a NUMBER so the table sorts, 0 only when unknown."""
    return round(float(runtime), 2) if runtime is not None else 0


def _per_test_rows(per_test: list[dict]) -> list[dict]:
    """One display row per test of an item: status icon, the failing/skip message."""
    return [
        {
            "collection": t.get("collection", ""),
            "test": t.get("test"),
            "status": _ICON.get(t.get("status", ""), t.get("status", "")),
            "message": t.get("message", ""),
        }
        for t in per_test
    ]


def main(per_item: list[dict] | None = None, vm_name: str = "") -> dict:
    items = list(per_item or [])
    # One shared rule with f/selftests/judge: collect already folds crashes,
    # timeouts, truncated KTAP and did-nothing runs into failed/notrun statuses.
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
            "collection": s.get("item") or "(step failed)",
            "tests": int(s.get("tests", 0) or 0),
            "passed": int(s.get("passed", 0) or 0),
            "failed": int(s.get("failed", 0) or 0),
            "skipped": int(s.get("skipped", 0) or 0),
            "time(s)": _time_s(s.get("runtime")),
            "status": _ICON.get(s.get("status", ""), s.get("status", "") or "❌"),
        }
        for s in items
    ]

    # Table 3: one row per test across all items, failures first.
    test_rows = [
        r
        for s in items
        for r in _per_test_rows((s.get("detail") or {}).get("per_test") or [])
    ]
    test_rows.sort(
        key=lambda r: (
            _RANK.get(r["status"], 3),
            r.get("collection") or "",
            r.get("test") or "",
        )
    )

    failures = [
        {
            "collection": f.get("collection", ""),
            "test": f.get("test"),
            "message": f.get("message", ""),
        }
        for s in items
        for f in ((s.get("detail") or {}).get("failures") or [])
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
            "testsuite": "selftests",
            "kernel": kernel_version or "?",
            "guest": vm_name or "?",
            "collections": len(items),
            "time(s)": round(sum(s.get("runtime") or 0 for s in items), 2),
        }
    ]
    return {"render_all": [run_info, item_rows, test_rows]}
