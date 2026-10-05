# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fail the run when a check failed or could not run.

Applies `f.kernel.checks.outcome` (the rule `f/kernel/report_checks` also uses) to the
check results and raises unless none failed, so a red series is a red
Windmill job. On a pass it returns the report unchanged, keeping its tables as
the flow result.
"""

from __future__ import annotations

from f.kernel.checks import outcome


def main(results: dict, report: dict | None = None) -> dict:
    bad = {
        (res or {}).get("check") or step: outcome(res)
        for step, res in results.items()
        if outcome(res) in ("failed", "error")
    }
    if bad:
        named = ", ".join(f"{name} ({status})" for name, status in bad.items())
        raise RuntimeError(f"{len(bad)} check(s) did not pass: {named}")
    print(f"all {len(results)} checks passed", flush=True)
    return report or {}
