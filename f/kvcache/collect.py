# SPDX-License-Identifier: copyleft-next-0.3.1
"""Collect one replay's results from the host side of the `kvcache` share.

Reads `<share>/<kver>/results/<preset>/{summary,failures}.json` -- written by
the guest's replayer, visible to every worker through virtiofs -- and returns
the parsed summary (TTFT/latency percentiles, token counts, failed-session
accounting, and the engine's lmcache_*/prefix-cache counter deltas). Zero
files is reported as `collected=False`, never as an empty pass: a run that
crashed before writing anything must stay visibly incomplete. Reads only;
mutates nothing.

Equivalent commands:

    cat .../shared/kvcache/<vm>/<kver>/results/<preset>/summary.json
"""

from __future__ import annotations

import json

from f.kvcache.common import list_vms as _list_vms
from f.kvcache.common import results_dir


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, preset: str, kernel_version: str) -> dict:
    out = results_dir(vm_name, kernel_version, preset)
    summary_path = out / "summary.json"
    if not summary_path.is_file():
        print(f"{vm_name}: no summary at {summary_path}", flush=True)
        return {
            "vm": vm_name,
            "preset": preset,
            "collected": False,
            "results_dir": str(out),
        }
    summary = json.loads(summary_path.read_text())
    failures = []
    fpath = out / "failures.json"
    if fpath.is_file():
        failures = json.loads(fpath.read_text())
    print(f"{vm_name}: collected {summary_path}", flush=True)
    return {
        "vm": vm_name,
        "preset": preset,
        "collected": True,
        "results_dir": str(out),
        "summary": summary,
        "failures": failures[:20],
        "failures_total": len(failures),
    }
