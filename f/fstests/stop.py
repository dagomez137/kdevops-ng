# SPDX-License-Identifier: copyleft-next-0.3.1
"""Stop xfstests@<section>.service unit(s) on a booted guest over vsock-SSH.

Wired as `check.flow`'s `failure_module` so cancelling a run from the Windmill
UI, or any step erroring out mid-section, tears the running unit down on the
guest instead of leaving `./check -s <section>` burning CPU under
`TimeoutStartSec=infinity`. Per-section units are independent, so the helper
iterates each section the run was driving and stops them all; the stops are
idempotent (an inactive unit is a no-op once `reset-failed` clears any latched
state). A guest unreachable from the worker is logged and skipped — the
failure handler must not itself fail.

Force-stopping a Windmill job (SIGKILL of the worker process) bypasses
`failure_module`, so it does not reach here; the manual fallback is
`systemctl --host <vm> stop xfstests@<section>.service`.

Equivalent commands, against the guest over vsock-SSH:

    ssh <vm> systemctl stop         xfstests@<section>.service
    ssh <vm> systemctl reset-failed xfstests@<section>.service
"""

from __future__ import annotations

import os
from pathlib import Path

from f.fstests.common import RemoteSystemd, list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name` — see `f.fstests.common.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, sections: list[str] | None = None) -> dict:
    sections = list(sections or [])
    if not vm_name or not sections:
        print(f"+ stop: nothing to do (vm_name={vm_name!r}, sections={sections})", flush=True)
        return {"vm_name": vm_name, "stopped": [], "skipped_no_transport": False}
    workers = Path(os.environ["WORKERS_DIR"])
    try:
        remote = RemoteSystemd(workers, vm_name)
    except Exception as exc:
        print(f"{vm_name}: cannot reach guest ({exc}); skipping stop", flush=True)
        return {"vm_name": vm_name, "stopped": [], "skipped_no_transport": True}
    stopped: list[str] = []
    for section in sections:
        unit = f"xfstests@{section}.service"
        print(f"+ systemctl stop {unit}", flush=True)
        remote.systemctl("stop", unit, check=False)
        print(f"+ systemctl reset-failed {unit}", flush=True)
        remote.systemctl("reset-failed", unit, check=False)
        stopped.append(unit)
    return {"vm_name": vm_name, "stopped": stopped, "skipped_no_transport": False}
