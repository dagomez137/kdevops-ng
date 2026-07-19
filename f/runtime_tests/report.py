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
import statistics

from f.runtime_tests.common import _atomic_write, cache_dir, run_status

_ICON = {"passed": "✅", "failed": "❌", "notrun": "⊘"}
# Failures first in the per-module summary table, then notruns, then passes.
_RANK = {"failed": 0, "notrun": 1, "passed": 2}


def _aggregate(items: list) -> list:
    """Fold the flow's `repeats` of one item into a single entry.

    Grouped by item name, keeping order of first appearance. `runtime`
    becomes the median across the runs, so every consumer of the rollup (the
    bisect's max_runtime, the sweep, the tables) reads the calculated value;
    the spread (`runtime_min`/`runtime_max`), the per-run list (`runs`), the
    sample count (`samples`), the derived `throughput` (passed per second at
    the median), and a `count_variance` flag (test counts differing between
    runs, a content instability signal) ride along. Status is the worst of
    the runs: a single flaky failure fails the item. A step-error entry (no
    dict, from skip_failures) stays its own failed row.
    """
    groups: dict[str, list[dict]] = {}
    order: list = []
    for s in items:
        if not isinstance(s, dict) or not s.get("item"):
            order.append(s)
            continue
        name = s["item"]
        if name not in groups:
            order.append(name)
            groups[name] = []
        groups[name].append(s)

    out = []
    for entry in order:
        if not isinstance(entry, str):
            out.append(entry)
            continue
        runs = groups[entry]
        # Counts and throughput come from the last PASSING run (an aborted
        # run reports zero tests), while the evidence fields come from the
        # last FAILING run: its detail (the assertion tail, the reasons)
        # is what a later passing run would otherwise silently discard.
        passing = [r for r in runs if r.get("status") == "passed"]
        failing = [r for r in runs if r.get("status") == "failed"]
        agg = dict((passing or runs)[-1])
        if failing:
            agg["detail"] = failing[-1].get("detail")
            agg["exec_status"] = failing[-1].get("exec_status")
        statuses = [r.get("status") for r in runs]
        agg["status"] = (
            "failed"
            if "failed" in statuses
            else "notrun"
            if "notrun" in statuses
            else statuses[-1]
        )
        times = [r["runtime"] for r in runs if r.get("runtime") is not None]
        if times:
            agg["runtime"] = round(statistics.median(times), 2)
            agg["runtime_min"] = round(min(times), 2)
            agg["runtime_max"] = round(max(times), 2)
        agg["samples"] = len(runs)
        agg["count_variance"] = len({r.get("tests") for r in runs}) > 1
        passed = int(agg.get("passed") or 0)
        agg["throughput"] = (
            round(passed / agg["runtime"], 1) if times and agg["runtime"] else 0
        )
        agg["runs"] = [
            {
                "status": r.get("status"),
                "runtime": r.get("runtime"),
                "tests": r.get("tests"),
                "passed": r.get("passed"),
                "failed": r.get("failed"),
            }
            for r in runs
        ]
        out.append(agg)
    return out


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
    # One shared rule with f/runtime_tests/judge: collect already folds
    # crashes, timeouts, skipped units and did-nothing runs into
    # failed/notrun statuses. Judge sees the raw run list; the rollup and
    # tables see one aggregated entry per item (median over `repeats`).
    status = run_status(list(per_item or []))
    items = _aggregate(list(per_item or []))
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
            "spread(s)": round(
                (s.get("runtime_max") or 0) - (s.get("runtime_min") or 0), 2
            ),
            "samples": int(s.get("samples", 1) or 1),
            "tests/s": s.get("throughput", 0),
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
