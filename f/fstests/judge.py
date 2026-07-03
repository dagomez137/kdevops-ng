# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fail the run when the xfstests verdict is not a pass.

Applies `f.fstests.common.run_status` (the one rule `f/fstests/report` also
uses) to the per-section collect results and raises unless every section
passed, so a red run is a red Windmill job: schedules, callers, and flows
embedding this one see the verdict in the job state, not only in the report
tables. On a pass it returns the report rollup unchanged, keeping the
report's tables as the flow result (`render_all` must stay the sole key to
render, which is why the verdict travels through the per-section results,
not the report).
"""

from __future__ import annotations

from f.fstests.common import run_status


def main(per_section: list[dict] | None = None, report: dict | None = None) -> dict:
    sections = list(per_section or [])
    status = run_status(sections)
    if status != "passed":
        bad = [s for s in sections if s.get("status") != "passed"]
        named = ", ".join(
            f"{s.get('section')} ({s.get('failed', 0)} failed, "
            f"{s.get('passed', 0)} passed)"
            for s in bad[:10]
        )
        detail = f": {named}" if named else " (no sections ran)"
        raise RuntimeError(
            f"fstests run failed: {len(bad)}/{len(sections)} section(s) not "
            f"passed{detail}"
        )
    print(f"run passed ({len(sections)} section(s))", flush=True)
    return report or {}
