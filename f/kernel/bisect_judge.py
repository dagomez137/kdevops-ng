# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fail the bisect run unless it reached a conclusion.

Reads the state `f/kernel/bisect_step` keeps and raises unless the run ended
with a real answer: `first_bad_found`, `not_reproducible_standalone` (the
suite only fails after others ran; itself the finding), or
`good_endpoint_failed` (the chosen good ref is not good; pick an older one).
`max_steps_exceeded`, `endpoint_untestable`, and `inconclusive` are red: the
loop stopped without an answer, and the report's `git bisect log` is the
resume point. A red run is a red Windmill job, mirroring the suite flows'
report/judge split.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_CONCLUSIVE = {"first_bad_found", "not_reproducible_standalone", "good_endpoint_failed"}


def main(vm_name: str) -> dict:
    sdir = Path(os.environ["SYSTEM_DIR"]) / "bisect" / vm_name
    state = json.loads((sdir / "state.json").read_text())
    outcome = state.get("outcome") or ""
    if outcome not in _CONCLUSIVE:
        raise RuntimeError(
            f"bisect did not conclude: outcome={outcome or 'none'} after "
            f"{state.get('steps', 0)} bisect step(s); see the report's bisect log"
        )
    print(f"conclusive: {outcome}", flush=True)
    return {"outcome": outcome, "first_bad": state.get("first_bad", "")}
