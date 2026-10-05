# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fold the check results into one report.

Writes every step's full result to `report.json` in `check/`, the manifest
of the run, and returns a Windmill `render_all` display of native tables
(`render_all` must stay the sole key or the display falls back to raw JSON):

  1. the series: base, commits, scope, patch dir, report path
  2. one row per check: status, errors, warnings, log
  3. every finding returned, errors first
  4. the recipients `get_maintainer.pl` listed

`f/kernel/judge_checks` turns the same results into the job verdict (`outcome` is the
one shared rule).

Equivalent command:

    cat "$WORKERS_DIR/$WORKER_INDEX/check/report.json"
"""

from __future__ import annotations

import json
from pathlib import Path

from f.kernel.checks import outcome

_ICON = {"passed": "✅", "failed": "❌", "error": "❌ error", "not run": "⊘"}
_RANK = {"error": 0, "warning": 1, "check": 2}


def main(
    series: dict,
    selection: dict,
    results: dict,
    maintainers: dict | None = None,
) -> dict:
    skipped = selection.get("skipped") or {}
    rows, findings = [], []
    for step, res in results.items():
        if not res:
            continue
        status = outcome(res)
        name = res.get("check") or step
        rows.append(
            {
                "check": name,
                "status": _ICON[status],
                "errors": int(res.get("errors", 0) or 0),
                "warnings": int(res.get("warnings", 0) or 0),
                "checks": int(res.get("checks", 0) or 0),
                "log": res.get("log") or ", ".join((res.get("logs") or {}).values()),
                "note": (res.get("error") or {}).get("message", "")
                if status == "error"
                else "",
            }
        )
        findings += [{"check": name, **f} for f in res.get("findings") or []]
    rows += [
        {"check": name, "status": _ICON["not run"], "note": why}
        for name, why in skipped.items()
    ]
    findings.sort(key=lambda f: (_RANK.get(f["level"], 3), f["check"], f["file"]))

    report = Path(series["check_dir"]) / "report.json"
    manifest = {
        "series": series,
        "selection": selection,
        "results": results,
        "maintainers": maintainers,
    }
    report.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {report}", flush=True)

    info = [
        {
            "base": series.get("base"),
            "base commit": (series.get("base_commit") or "")[:12],
            "commits": series.get("commits"),
            "scope": selection.get("scope"),
            "patches": series.get("dir"),
            "report": str(report),
        }
    ]
    recipients = [{"recipient": e} for e in (maintainers or {}).get("entries") or []]
    return {"render_all": [info, rows, findings, recipients]}
