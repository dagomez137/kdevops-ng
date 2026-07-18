# SPDX-License-Identifier: copyleft-next-0.3.1
"""Wait until a booted guest answers over vsock-SSH and finishes startup.

`f/qsu/boot` returns as soon as the `qemu-system@<vm>.service` unit is
active, polling SSH only briefly, so a fresh guest can still be activating
its closure (sshd not yet up) when the next flow's gate probes it. This step
closes that race for any composition that runs a suite right after a
bringup: poll `systemctl --host <vm> is-system-running` until the guest
reports `running` or `degraded`, then return. Expiry raises, so a kernel
that never brings the guest up fails the step rather than handing a dead
guest to the next flow.

Equivalent command, repeated until it answers:

    systemctl --host <vm> is-system-running
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from f.common.remote import RemoteSystemd
from f.common.remote import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, timeout: int = 240, poll_interval: int = 5) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    start = time.monotonic()
    deadline = start + max(1, int(timeout))
    state = ""
    polls = 0
    while time.monotonic() < deadline:
        polls += 1
        state = remote.is_system_running(quiet=True)
        if state in ("running", "degraded"):
            waited = round(time.monotonic() - start, 1)
            print(f"{vm_name}: {state} after {waited}s ({polls} polls)", flush=True)
            return {"vm_name": vm_name, "state": state, "polls": polls}
        time.sleep(max(1, int(poll_interval)))
    raise RuntimeError(
        f"{vm_name}: not up within {timeout}s "
        f"(last is-system-running={state!r} over {polls} polls)"
    )
