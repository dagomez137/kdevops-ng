# SPDX-License-Identifier: copyleft-next-0.3.1
"""Render the bisect run: outcome, per-iteration verdicts, first bad commit.

Reads the `state.json` the `f/kernel/bisect_step` state machine keeps under
`$SYSTEM_DIR/bisect/<vm_name>/` and renders the run: one summary row
(outcome, endpoints, steps), one row per iteration (phase, candidate,
verdict), and, when the bisect concluded, the first bad commit named with
its subject plus the full `git bisect log` so a manual `git bisect` can
resume from the same state clone. Renders only; `f/kernel/bisect_judge`
owns the verdict.

Equivalent commands:

    cat "$SYSTEM_DIR/bisect/<vm_name>/state.json"
    git -C "$SYSTEM_DIR/bisect/<vm_name>/repo" bisect log
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from f.common.devshell import Git


def main(vm_name: str) -> dict:
    sdir = Path(os.environ["SYSTEM_DIR"]) / "bisect" / vm_name
    state = json.loads((sdir / "state.json").read_text())
    repo = sdir / "repo"
    git = Git(Path(os.environ["WORKERS_DIR"]))

    outcome = state.get("outcome") or "(still running)"
    first_bad = state.get("first_bad", "")
    summary = [
        {
            "outcome": outcome,
            "bad": f"{state.get('bad')} ({state.get('bad_sha', '')[:12]})",
            "good": f"{state.get('good')} ({state.get('good_sha', '')[:12]})",
            "suites": ", ".join(state.get("suites") or []),
            "bisect steps": state.get("steps", 0),
        }
    ]
    rows = [
        {
            "phase": it.get("phase"),
            "candidate": (it.get("candidate") or "")[:12],
            "verdict": it.get("verdict"),
        }
        for it in state.get("iterations") or []
    ]

    if first_bad:
        subject = git.capture(
            "-C",
            str(repo),
            "log",
            "--max-count=1",
            "--format=%h %s",
            first_bad,
            check=False,
        ).strip()
        print(f"first bad commit: {subject or first_bad}", flush=True)
    if state.get("phase") == "bisect":
        log = git.capture("-C", str(repo), "bisect", "log", check=False)
        if log.strip():
            print(log.strip(), flush=True)

    print(f"outcome={outcome} iterations={len(rows)}", flush=True)
    return {"render_all": [summary, rows]}
