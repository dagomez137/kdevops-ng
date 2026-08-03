# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fail the run when the blktests verdict is not a pass.

Applies `f.blktests.common.run_status` (the one rule `f/blktests/report` also
uses) to the per-group collect results and raises unless every group passed,
so a red run is a red Windmill job: schedules, callers, and flows embedding
this one see the verdict in the job state, not only in the report tables. On a
pass it returns the report rollup unchanged, keeping the report's tables as
the flow result (`render_all` must stay the sole key to render, which is why
the verdict travels through the per-group results, not the report).
"""

from __future__ import annotations

from f.blktests.common import run_status


def main(per_group: list[dict] | None = None, report: dict | None = None) -> dict:
    groups = list(per_group or [])
    status = run_status(groups)
    if status != "passed":
        bad = [
            g
            for g in groups
            if not (isinstance(g, dict) and g.get("status") == "passed")
        ]
        named = ", ".join(
            f"{g.get('group')} ({(g.get('stats') or {}).get('fail', 0)} failed, "
            f"{(g.get('stats') or {}).get('pass', 0)} passed)"
            for g in bad[:10]
            if isinstance(g, dict)
        )
        detail = f": {named}" if named else " (no groups ran)"
        raise RuntimeError(
            f"blktests run failed: {len(bad)}/{len(groups)} group(s) not passed{detail}"
        )
    print(f"run passed ({len(groups)} group(s))", flush=True)
    return report or {}
