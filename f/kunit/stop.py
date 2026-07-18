# SPDX-License-Identifier: copyleft-next-0.3.1
"""Stop kunit@<suite>.service unit(s) on a booted guest over vsock-SSH.

Wired as `run.flow`'s `failure_module` so cancelling a run from the Windmill UI, or
any step erroring out mid-suite, tears the running unit down on the guest instead of
leaving a hung suite spinning. Per-suite units are independent, so the helper
iterates each suite the run was driving and stops both its possible templates
(`kunit@` and the init-only `kunit-results@`); the stops are idempotent (an
inactive unit is a no-op once `reset-failed` clears any latched state). A guest
unreachable from the worker is logged and skipped; the failure handler must not
itself fail.

Force-stopping a Windmill job (SIGKILL of the worker process) bypasses
`failure_module`, so it does not reach here; the manual fallback is
`systemctl --host <vm> stop kunit@<suite>.service`.

Equivalent commands, against the guest over vsock-SSH:

    ssh <vm> systemctl stop         kunit@<suite>.service
    ssh <vm> systemctl reset-failed kunit@<suite>.service
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.remote import RemoteSystemd
from f.common.remote import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def list_suites(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_suites` entrypoint: see `f.kunit.common.list_suites`."""
    from f.kunit.common import list_suites as _list_suites

    return _list_suites(vm_name, filterText)


def main(vm_name: str, suites: list[str] | None = None) -> dict:
    suites = list(suites or [])
    if not vm_name or not suites:
        print(
            f"+ stop: nothing to do (vm_name={vm_name!r}, suites={suites})",
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
    for suite in suites:
        for template in ("kunit", "kunit-results"):
            unit = f"{template}@{suite}.service"
            remote.systemctl("stop", unit, check=False)
            remote.systemctl("reset-failed", unit, check=False)
            stopped.append(unit)
    return {"vm_name": vm_name, "stopped": stopped, "skipped_no_transport": False}
