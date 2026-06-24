# SPDX-License-Identifier: copyleft-next-0.3.1
"""Start one xfstests section on a booted guest over vsock-SSH (fire-and-forget).

Starts `xfstests@<section>.service` on the guest with `--no-block`: the unit is
`Type=oneshot`, so a blocking `start` would not return until the whole section's
`./check` run finished (hours). `--no-block` returns immediately; `f/fstests/wait`
polls for the outcome. After starting, we read back `ActiveState` and assert it is
`activating`/`active` so a start that never took (e.g. a bad section) fails here
rather than silently in the wait step.

Equivalent commands, against the guest over vsock-SSH:

    systemctl --host <vm> start --no-block xfstests@<section>.service
    systemctl --host <vm> show xfstests@<section>.service --property=ActiveState
"""

from __future__ import annotations

import os
from pathlib import Path

from f.fstests.common import RemoteSystemd, list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.fstests.common.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, section: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    unit = f"xfstests@{section}.service"

    remote.systemctl("start", "--no-block", unit)
    active_state = remote.show(unit, "ActiveState").get("ActiveState", "")
    if active_state not in ("activating", "active"):
        raise RuntimeError(
            f"{vm_name}: {unit} did not start (ActiveState={active_state!r}, "
            f"expected activating/active)"
        )

    print(f"{vm_name}: started {unit} (ActiveState={active_state})", flush=True)
    return {"vm": vm_name, "section": section, "unit": unit, "active_state": active_state}
