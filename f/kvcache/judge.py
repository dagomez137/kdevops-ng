# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fail the flow unless the replay ran and completed successfully.

The single gate downstream consumers rely on: `wait` must have observed a
`success` terminus (Result=success, ExecMainStatus=0 -- the replayer's own
failed-session-rate gate is inside that exit code) and `collect` must have
found a summary. Raising here fails the Windmill job, so a red run is
meaningful rather than advisory. Pure function of its inputs.
"""

from __future__ import annotations


def main(wait: dict, collected: dict) -> dict:
    problems: list[str] = []
    if wait.get("crashed"):
        problems.append("guest crashed mid-run (host qemu-system unit not active)")
    if wait.get("timed_out"):
        problems.append("run exceeded the wait timeout")
    if not wait.get("success"):
        problems.append(
            f"unit terminus not success (Result={wait.get('result')!r}, "
            f"ExecMainStatus={wait.get('exec_main_status')!r})"
        )
    if not collected.get("collected"):
        problems.append("no summary.json on the share (replayer wrote nothing)")
    if problems:
        raise RuntimeError(
            f"kvcache run failed for {wait.get('vm')}/{wait.get('preset')}: "
            + "; ".join(problems)
        )
    return {"vm": wait.get("vm"), "preset": wait.get("preset"), "verdict": "pass"}
