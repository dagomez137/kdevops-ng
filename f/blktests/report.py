# SPDX-License-Identifier: copyleft-next-0.3.1
"""Aggregate the per-group blktests results into one run verdict.

Folds the list of `f/blktests/collect` results (one per group, collected by the
flow's per-group forloop) into a single rollup: the per-group summaries, the
per-test rows, and a `status` of `passed` only when every group passed (a
failed, crashed, or notrun group fails the run, and so does an empty group
list: aggregating nothing must never read as a pass). Pure aggregation, no
guest contact. `f/blktests/judge` turns the same per-group results into the job
verdict (`run_status` is the one shared rule). When a `vm_name` is given it
also writes the full rollup to `<share>/<kver>/report.json` (atomically), so
the run's verdict is recoverable from the share alone.

The returned value is a Windmill `render_all` display of three NATIVE tables
(no markdown: arrays of objects render as real sortable/searchable tables;
`render_all` must stay the sole key or the display falls back to raw JSON):

  1. run info: testsuite, kernel, guest
  2. per group: pass/fail/notrun counts, crash/timeout flags, status
  3. per test: one row per (devdir, test) across all groups (failures first)

Equivalent command:

    cat "$WORKERS_DIR/shared/blktests/<vm>/<kver>/report.json"   # when vm_name is given
"""

from __future__ import annotations

import json

from f.blktests.common import _atomic_write, report_path, run_status

# Cap the per-test table; the full row list is always in report.json.
_TEST_TABLE_CAP = 1000
_ICON = {"passed": "✅", "failed": "❌", "notrun": "⊘"}
_ROW_ICON = {"pass": "✅", "fail": "❌", "not run": "⊘", "missing": "❌"}
# Failures first in the per-test table (a `missing` row is failure-adjacent),
# then the notruns, then the passes.
_RANK = {"fail": 0, "missing": 1, "not run": 2, "pass": 3}


def main(per_group: list[dict] | None = None, vm_name: str = "") -> dict:
    groups = list(per_group or [])
    # One shared rule with f/blktests/judge: collect already folds a crash, a
    # timeout, a dirty unit exit, and a zero-file notrun into each group's status.
    status = run_status(groups)
    kernel_version = next(
        (
            g.get("kernel_version")
            for g in groups
            if isinstance(g, dict) and g.get("kernel_version")
        ),
        "",
    )

    # Table 2: one row per group: its counts, run-outcome flags, and verdict
    # icon. The forloop runs with skip_failures, so a group whose step errored
    # hard (an SSH failure in start) arrives as an error object, not a collect
    # result; render it as a failed row rather than a blank one (run_status
    # already fails the run).
    group_rows = []
    for g in groups:
        entry = g if isinstance(g, dict) else {}
        stats = entry.get("stats") or {}
        group_rows.append(
            {
                "group": entry.get("group") or "(step failed)",
                "pass": int(stats.get("pass", 0) or 0),
                "fail": int(stats.get("fail", 0) or 0),
                "notrun": int(stats.get("notrun", 0) or 0),
                "crashed": bool(entry.get("crashed", False)),
                "timed_out": bool(entry.get("timed_out", False)),
                "status": _ICON.get(
                    entry.get("status", ""), entry.get("status", "") or "❌"
                ),
            }
        )

    # Table 3: one row per (devdir, test) across all groups, failures first.
    raw = [
        (g.get("group"), r)
        for g in groups
        if isinstance(g, dict)
        for r in (g.get("rows") or [])[:_TEST_TABLE_CAP]
    ]
    raw.sort(
        key=lambda item: (
            _RANK.get(item[1].get("status"), 0),
            item[0] or "",
            item[1].get("devdir") or "",
            item[1].get("test") or "",
        )
    )
    test_rows = [
        {
            "group": group_name,
            "devdir": r.get("devdir", ""),
            "test": r.get("test"),
            "status": _ROW_ICON.get(r.get("status", ""), r.get("status", "")),
            "reason": r.get("reason", ""),
            "time(s)": r.get("runtime") if r.get("runtime") is not None else 0,
        }
        for group_name, r in raw
    ]

    # report.json keeps the full structured rollup, one flat row per failing test.
    failures = [
        {
            "group": g.get("group"),
            "devdir": r.get("devdir", ""),
            "test": r.get("test"),
            "status": r.get("status", ""),
            "reason": r.get("reason", ""),
        }
        for g in groups
        if isinstance(g, dict)
        for r in (g.get("rows") or [])
        if r.get("status") not in ("pass", "not run")
    ]
    rollup = {
        "status": status,
        "kernel_version": kernel_version,
        "groups": groups,
        "failures": failures,
    }
    print(
        f"status={status} groups={len(groups)} rows={len(raw)} failing={len(failures)}",
        flush=True,
    )

    if vm_name:
        path = report_path(vm_name, kernel_version)
        _atomic_write(path, json.dumps(rollup, indent=2) + "\n")
        print(f"+ wrote {path}", flush=True)
        rollup["report_json"] = str(path)

    # render_all (must be the sole key): three native tables, no markdown: run
    # info, the per-group counts, then one row per (devdir, test).
    run_info = [
        {
            "testsuite": "blktests",
            "kernel": kernel_version or "?",
            "guest": vm_name or "?",
        }
    ]
    return {"render_all": [run_info, group_rows, test_rows]}
