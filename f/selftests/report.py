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
the run's verdict is recoverable from the share alone. The flow's `repeats` of
one item fold into a single entry (median runtime, worst status) for the rollup
and the tables; the verdict still sees every run.

The returned value is a Windmill `render_all` display of three NATIVE tables (no
markdown: arrays of objects render as real sortable/searchable tables;
`render_all` must stay the sole key or the display falls back to raw JSON):

  1. run info: testsuite, kernel, guest, collections, time(s)
  2. per item: collection, tests, passed, failed, skipped, crashes, time(s),
     spread(s), samples, status
  3. per test: one row per test across all items (failures first)

Equivalent command:

    cat "$WORKERS_DIR/shared/selftests/<vm>/<kver>/report.json"   # when vm_name is given
"""

from __future__ import annotations

import json
import statistics

from f.selftests.common import _atomic_write, run_status, share_dir

_ICON = {"passed": "✅", "failed": "❌", "notrun": "⊘"}
# Failures first in the per-test table, then notruns, then passes.
_RANK = {"failed": 0, "notrun": 1, "passed": 2}


def _aggregate(items: list) -> list:
    """Fold the flow's `repeats` of one item into a single entry.

    Grouped by item name, keeping order of first appearance. `runtime` becomes
    the median across the runs, so every consumer of the rollup (the bisect's
    max_runtime, the tables) reads the calculated value; the spread
    (`runtime_min`/`runtime_max`), the per-run list (`runs`), the sample count
    (`samples`) and a `count_variance` flag (test counts differing between
    runs) ride along. Status is the worst of the runs: a single flaky failure
    fails the item. Counts come from the last passing run, the `detail`
    evidence (per-test rows, `failures`, crash lines, KTAP) from the last
    failing run, else the last run that logged a userspace crash, so a later
    clean run never hides it. `user_crashes` sums the runs' known counts
    (`None` when no run's scan succeeded). A step-error entry (no dict, from
    skip_failures) stays its own failed row.
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
        passing = [r for r in runs if r.get("status") == "passed"]
        failing = [r for r in runs if r.get("status") == "failed"]
        crashing = [r for r in runs if r.get("user_crashes")]
        agg = dict((passing or runs)[-1])
        if evidence := (failing or crashing):
            agg["detail"] = evidence[-1].get("detail")
            agg["exec_status"] = evidence[-1].get("exec_status")
        statuses = [r.get("status") for r in runs]
        agg["status"] = (
            "failed"
            if "failed" in statuses
            else "notrun"
            if "notrun" in statuses
            else statuses[-1]
        )
        counts = [r["user_crashes"] for r in runs if r.get("user_crashes") is not None]
        agg["user_crashes"] = sum(counts) if counts else None
        times = [r["runtime"] for r in runs if r.get("runtime") is not None]
        if times:
            agg["runtime"] = round(statistics.median(times), 2)
            agg["runtime_min"] = round(min(times), 2)
            agg["runtime_max"] = round(max(times), 2)
        agg["samples"] = len(runs)
        agg["count_variance"] = len({r.get("tests") for r in runs}) > 1
        agg["runs"] = [
            {
                "status": r.get("status"),
                "runtime": r.get("runtime"),
                "tests": r.get("tests"),
                "passed": r.get("passed"),
                "failed": r.get("failed"),
                "user_crashes": r.get("user_crashes"),
            }
            for r in runs
        ]
        out.append(agg)
    return out


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
    # One shared rule with f/selftests/judge: collect already folds crashes,
    # timeouts, truncated KTAP and did-nothing runs into failed/notrun statuses.
    # Judge sees the raw run list; the rollup and tables see one aggregated
    # entry per item (median over `repeats`).
    status = run_status(list(per_item or []))
    items = _aggregate(list(per_item or []))
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
            "crashes": s.get("user_crashes"),
            "time(s)": _time_s(s.get("runtime")),
            "spread(s)": round(
                (s.get("runtime_max") or 0) - (s.get("runtime_min") or 0), 2
            ),
            "samples": int(s.get("samples", 1) or 1),
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
