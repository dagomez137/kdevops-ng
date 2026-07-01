# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fail the run when the KUnit verdict is not a pass.

Applies `f.kunit.common.run_status` (the one rule `f/kunit/report` also uses)
to the per-suite collect results and raises unless every suite passed, so a
red run is a red Windmill job: schedules, callers, and flows embedding this
one see the verdict in the job state, not only in the report tables. On a
pass it returns the report rollup unchanged, keeping the report's tables as
the flow result (`render_all` must stay the sole key to render, which is why
the verdict travels through the per-suite results, not the report).
"""

from __future__ import annotations

from f.kunit.common import run_status


def main(per_suite: list[dict] | None = None, report: dict | None = None) -> dict:
    suites = list(per_suite or [])
    status = run_status(suites)
    if status != "passed":
        bad = [s for s in suites if s.get("status") != "passed"]
        named = ", ".join(
            f"{s.get('suite')} ({s.get('status')}: {s.get('failed', 0)} failed, "
            f"{s.get('passed', 0)} passed)"
            for s in bad[:10]
        )
        detail = f": {named}" if named else " (no suites ran)"
        raise RuntimeError(
            f"kunit run failed: {len(bad)}/{len(suites)} suite(s) not passed{detail}"
        )
    print(f"run passed ({len(suites)} suite(s))", flush=True)
    return report or {}
