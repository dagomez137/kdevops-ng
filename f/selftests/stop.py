# SPDX-License-Identifier: copyleft-next-0.3.1
"""Stop kselftest@<instance>.service unit(s) on a booted guest over vsock-SSH.

Wired as `run.flow`'s `failure_module` so cancelling a run from the Windmill UI,
or any step erroring out mid-run, tears the running unit down on the guest
instead of leaving a hung collection spinning. Per-item units are independent,
so the helper iterates each item the run was driving and stops both its
possible templates (`kselftest@` and the single-test `kselftest-test@`), the
instance systemd-escaped exactly as `f/selftests/start` escapes it; the stops
are idempotent (an inactive unit is a no-op once `reset-failed` clears any
latched state). A guest unreachable from the worker is logged and skipped; the
failure handler must not itself fail.

Force-stopping a Windmill job (SIGKILL of the worker process) bypasses
`failure_module`, so it does not reach here; the manual fallback is
`systemctl --host <vm> stop kselftest@<instance>.service`.

Equivalent commands, against the guest over vsock-SSH:

    ssh <vm> systemctl stop         kselftest@<instance>.service
    ssh <vm> systemctl reset-failed kselftest@<instance>.service
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.remote import RemoteSystemd
from f.common.remote import list_vms as _list_vms
from f.selftests.common import UNIT_TEMPLATES, unit_escape


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, items: list[str] | None = None) -> dict:
    items = list(items or [])
    if not vm_name or not items:
        print(
            f"+ stop: nothing to do (vm_name={vm_name!r}, items={items})",
            flush=True,
        )
        return {"vm_name": vm_name, "stopped": [], "skipped_no_transport": False}
    workers = Path(os.environ["WORKERS_DIR"])
    try:
        remote = RemoteSystemd(workers, vm_name)
    except Exception as exc:
        print(f"{vm_name}: cannot reach guest ({exc}); skipping stop", flush=True)
        return {"vm_name": vm_name, "stopped": [], "skipped_no_transport": True}
    stopped: list[str] = []
    for item in items:
        for template in UNIT_TEMPLATES:
            unit = f"{template}@{unit_escape(item)}.service"
            remote.systemctl("stop", unit, check=False)
            remote.systemctl("reset-failed", unit, check=False)
            stopped.append(unit)
    return {"vm_name": vm_name, "stopped": stopped, "skipped_no_transport": False}
