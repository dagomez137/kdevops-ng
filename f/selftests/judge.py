# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fail the run when the kselftest verdict is not a pass.

Applies `f.selftests.common.run_status` (the one rule `f/selftests/report` also
uses) to the per-item collect results and raises unless every item passed, so a
red run is a red Windmill job: schedules, callers, and flows embedding this
one see the verdict in the job state, not only in the report tables. On a
pass it returns the report rollup unchanged, keeping the report's tables as
the flow result (`render_all` must stay the sole key to render, which is why
the verdict travels through the per-item results, not the report).
"""

from __future__ import annotations

from f.selftests.common import run_status


def main(per_item: list[dict] | None = None, report: dict | None = None) -> dict:
    items = list(per_item or [])
    status = run_status(items)
    if status != "passed":
        bad = [
            s for s in items if not isinstance(s, dict) or s.get("status") != "passed"
        ]
        named = ", ".join(
            f"{s.get('item')} ({s.get('status')}: {s.get('failed', 0)} failed, "
            f"{s.get('passed', 0)} passed)"
            for s in bad[:10]
            if isinstance(s, dict)
        )
        detail = f": {named}" if named else " (no items ran)"
        raise RuntimeError(
            f"selftests run failed: {len(bad)}/{len(items)} item(s) not passed{detail}"
        )
    print(f"run passed ({len(items)} item(s))", flush=True)
    return report or {}
