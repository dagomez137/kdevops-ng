#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
"""Smoke the suite flows' isolated steps as Windmill preview jobs.

Each case runs one flow module in isolation with `wmill flow preview --step`:
the local, undeployed step body executes on a real worker with degrade-inducing
or fixture arguments chosen so no guest and no share is touched (report cases
omit `vm_name`, which skips the rollup write; collect cases carry their
evidence inline or name a VM that cannot exist). The asserted contracts are the
ones the flows lean on: a crashed run can never read as a pass, an empty run is
failed, `report` renders `render_all` as the sole key, and `judge` turns a red
run into a red job while passing a green report through unchanged.

A preview resolves `from f...` imports against the DEPLOYED workspace modules,
so a shared-module fix shows up here only after `wmill sync push`; a red case
against an undeployed fix is the harness catching exactly that skew.

Equivalent command, per case:

    wmill flow preview f/<suite>/<flow>.flow --step <id> --data '<json>' --silent
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

# suite, flow directory, forloop result key, per-item argument name.
SUITES = [
    ("fstests", "f/fstests/check.flow", "per_section", "section"),
    ("kunit", "f/kunit/run.flow", "per_suite", "suite"),
    ("selftests", "f/selftests/run.flow", "per_item", "item"),
    ("runtime_tests", "f/runtime_tests/run.flow", "per_item", "module"),
    ("usertests", "f/usertests/run.flow", "per_item", "item"),
]

# The judge pass-through sentinel: on a green run judge must return the report
# it was handed, byte for byte.
GREEN_REPORT = {"render_all": [{"markdown": "preview-smoke sentinel"}]}


@dataclass
class Case:
    name: str
    flow: str
    step: str
    args: dict
    check: Callable[[dict], str | None]


def check_render_all(result: dict) -> str | None:
    if not isinstance(result, dict) or list(result) != ["render_all"]:
        return f"expected render_all as the sole key, got {list(result)!r}"
    if not isinstance(result["render_all"], list) or not result["render_all"]:
        return "expected a non-empty render_all list"
    return None


def check_error(result: dict) -> str | None:
    if not isinstance(result, dict) or "error" not in result:
        return f"expected the step to raise, got {result!r}"
    return None


def check_green_passthrough(result: dict) -> str | None:
    if result != GREEN_REPORT:
        return f"expected the report passed through unchanged, got {result!r}"
    return None


def check_crashed_fails(result: dict) -> str | None:
    if not isinstance(result, dict) or "error" in result:
        return f"expected a degraded result dict, got {result!r}"
    if result.get("status") != "failed":
        return f"expected status failed for a crashed run, got {result.get('status')!r}"
    return None


def cases() -> list[Case]:
    out = []
    for suite, flow, per_key, item_arg in SUITES:
        passing = {"status": "passed", item_arg: "smoke"}
        failing = {"status": "failed", "failed": 1, item_arg: "smoke"}
        hard_error = {"error": {"name": "SSH", "message": "poll failed"}}
        out += [
            Case(
                f"{suite}:report-empty", flow, "report", {per_key: []}, check_render_all
            ),
            Case(
                f"{suite}:report-rows",
                flow,
                "report",
                {per_key: [passing, failing, hard_error]},
                check_render_all,
            ),
            Case(
                f"{suite}:judge-red", flow, "judge", {per_key: [failing]}, check_error
            ),
            Case(
                f"{suite}:judge-green",
                flow,
                "judge",
                {per_key: [passing], "report": GREEN_REPORT},
                check_green_passthrough,
            ),
            Case(
                f"{suite}:collect-crashed",
                flow,
                "collect",
                {
                    "vm_name": "preview-smoke",
                    item_arg: "smoke",
                    "kernel_version": "0.0.0-smoke",
                    "crashed": True,
                },
                check_crashed_fails,
            ),
        ]
    return out


def run_case(case: Case) -> str | None:
    argv = [
        "wmill",
        "flow",
        "preview",
        case.flow,
        "--step",
        case.step,
        "--data",
        json.dumps(case.args),
        "--silent",
    ]
    print("+ " + " ".join(argv), flush=True)
    proc = subprocess.run(argv, capture_output=True, text=True)
    body = proc.stdout.strip().splitlines()
    try:
        result = json.loads(body[-1]) if body else None
    except json.JSONDecodeError:
        result = None
    if result is None:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        return f"no JSON result (exit {proc.returncode}): {' | '.join(tail)}"
    return case.check(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--only",
        default="",
        help="Run only the cases whose name contains this substring.",
    )
    opts = parser.parse_args()
    selected = [c for c in cases() if opts.only in c.name]
    if not selected:
        print(f"no case matches {opts.only!r}", file=sys.stderr)
        return 2
    failures = []
    for case in selected:
        why = run_case(case)
        verdict = "PASS" if why is None else f"FAIL: {why}"
        print(f"{case.name}: {verdict}", flush=True)
        if why is not None:
            failures.append(case.name)
    print(f"{len(selected) - len(failures)}/{len(selected)} cases passed", flush=True)
    if failures:
        print("failed: " + ", ".join(failures), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
