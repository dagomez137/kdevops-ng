# SPDX-License-Identifier: copyleft-next-0.3.1
"""Aggregate the per-module runtime-tests results into one run verdict.

Folds the list of `f/runtime_tests/collect` results (one per module, collected
by the flow's per-item forloop) into a single rollup: the per-module
summaries, one synthetic row per module, and a `status` of `passed` only when
every module passed (a failed, crashed, or notrun module fails the run, and so
does an empty list: aggregating nothing must never read as a pass). Pure
aggregation, no guest contact. `f/runtime_tests/judge` turns the same
per-module results into the job verdict (`run_status` is the one shared rule).
When a `vm_name` is given it also writes the full rollup to the host cache
dir's `<kver>/report.json` (atomically), so the run's verdict is recoverable
from the cache alone.

The returned value is a Windmill `render_all` display of three NATIVE tables
(no markdown: arrays of objects render as real sortable/searchable tables;
`render_all` must stay the sole key or the display falls back to raw JSON):

  1. run info: testsuite, kernel, guest, modules, time(s)
  2. per module: module, tests, passed, failed, skipped, time(s), status
  3. per-module summary rows across the run (failures first)

Equivalent command:

    cat "$WORKERS_DIR/shared/runtime-tests/<vm>/<kver>/report.json"
"""

from __future__ import annotations

import json

from f.runtime_tests.common import _atomic_write, cache_dir, run_status

_ICON = {"passed": "✅", "failed": "❌", "notrun": "⊘"}
# Failures first in the per-module summary table, then notruns, then passes.
_RANK = {"failed": 0, "notrun": 1, "passed": 2}


def _time_s(runtime: float | None) -> float | int:
    """The `time(s)` column: a NUMBER so the table sorts, 0 only when unknown."""
    return round(float(runtime), 2) if runtime is not None else 0


def _per_test_rows(per_test: list[dict]) -> list[dict]:
    """One display row per module summary: status icon, the failing message."""
    return [
        {
            "module": t.get("module", ""),
            "test": t.get("test"),
            "status": _ICON.get(t.get("status", ""), t.get("status", "")),
            "message": t.get("message", ""),
        }
        for t in per_test
    ]


def main(per_item: list[dict] | None = None, vm_name: str = "") -> dict:
    items = list(per_item or [])
    # One shared rule with f/runtime_tests/judge: collect already folds
    # crashes, timeouts, skipped units and did-nothing runs into
    # failed/notrun statuses.
    status = run_status(items)
    kernel_version = next(
        (s.get("kernel_version") for s in items if s.get("kernel_version")), ""
    )

    # Table 2: one row per module: its counts and verdict icon. The forloop
    # runs with skip_failures, so an item whose step errored hard (an SSH
    # failure in start) arrives as an error object, not a collect result;
    # render it as a failed row rather than a blank one (run_status already
    # fails the run).
    item_rows = [
        {
            "module": s.get("item") or "(step failed)",
            "tests": int(s.get("tests", 0) or 0),
            "passed": int(s.get("passed", 0) or 0),
            "failed": int(s.get("failed", 0) or 0),
            "skipped": int(s.get("skipped", 0) or 0),
            "time(s)": _time_s(s.get("runtime")),
            "status": _ICON.get(s.get("status", ""), s.get("status", "") or "❌"),
        }
        for s in items
    ]

    # Table 3: one summary row per module across the run, failures first.
    test_rows = [
        r
        for s in items
        for r in _per_test_rows((s.get("detail") or {}).get("per_test") or [])
    ]
    test_rows.sort(
        key=lambda r: (
            _RANK.get(r["status"], 3),
            r.get("module") or "",
            r.get("test") or "",
        )
    )

    failures = [
        {
            "module": s.get("item", ""),
            "reasons": (s.get("detail") or {}).get("reasons") or [],
        }
        for s in items
        if isinstance(s, dict) and s.get("status") == "failed"
    ]
    rollup = {
        "status": status,
        "kernel_version": kernel_version,
        "items": items,
        "failures": failures,
    }
    print(
        f"status={status} modules={len(items)} "
        f"tests={sum(r['tests'] for r in item_rows)} failing={len(failures)}",
        flush=True,
    )

    if vm_name:
        # Key the aggregate by kernel too (results are kver-keyed), so two
        # kernels' runs on one guest don't clobber each other's report.json;
        # fall back to the cache root when the kernel is unknown (degraded run).
        out_dir = (
            cache_dir(vm_name) / kernel_version
            if kernel_version
            else cache_dir(vm_name)
        )
        path = out_dir / "report.json"
        _atomic_write(path, json.dumps(rollup, indent=2) + "\n")
        print(f"+ wrote {path}", flush=True)
        rollup["report_json"] = str(path)

    run_info = [
        {
            "testsuite": "runtime-tests",
            "kernel": kernel_version or "?",
            "guest": vm_name or "?",
            "modules": len(items),
            "time(s)": round(sum(s.get("runtime") or 0 for s in items), 2),
        }
    ]
    return {"render_all": [run_info, item_rows, test_rows]}
