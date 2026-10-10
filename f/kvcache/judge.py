# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fail the run when the kvcache verdict is not a pass.

Applies `f.kvcache.common.run_status` (the one rule f/kvcache/report also uses)
to the per-preset collect results and raises unless every preset passed, so a
red run is a red Windmill job. The verdict is about the numbers existing and
few enough sessions failing, never about their size. On a pass it returns the
report unchanged, keeping its tables as the flow result.
"""

from __future__ import annotations

from f.kvcache.common import counted_failure_rate, run_status


def _rate(item: dict) -> float | None:
    summary = item.get("summary")
    return round(counted_failure_rate(summary), 4) if summary else None


def main(per_preset: list[dict] | None = None, report: dict | None = None) -> dict:
    presets = list(per_preset or [])
    if run_status(presets) != "passed":
        bad = [
            p
            for p in presets
            if not (isinstance(p, dict) and p.get("status") == "passed")
        ]
        named = ", ".join(
            f"{p.get('preset')} ({p.get('status')}, failure rate {_rate(p)}"
            + (f"; {p['reason']}" if p.get("reason") else "")
            + ")"
            for p in bad[:10]
            if isinstance(p, dict)
        )
        detail = f": {named}" if named else " (no preset ran)"
        raise RuntimeError(
            f"kvcache run failed: {len(bad)}/{len(presets)} preset(s) not passed"
            f"{detail}"
        )
    print(f"run passed ({len(presets)} preset(s))", flush=True)
    return report or {}
