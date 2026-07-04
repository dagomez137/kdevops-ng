# SPDX-License-Identifier: copyleft-next-0.3.1
"""Stop modprobe@<module>.service unit(s) and unload the modules, over vsock-SSH.

Wired as `run.flow`'s `failure_module` so cancelling a run from the Windmill
UI, or any step erroring out mid-run, tears the run down on the guest instead
of leaving a wedged module load spinning. For each module the run was driving:
stop its `modprobe@` instance, `reset-failed` to clear any latched state (the
expected-error class latches one every run), and `modprobe --remove --quiet`
so a stay-loaded exit-honest module does not turn the next run into a no-op
(a loaded module neither re-runs its tests nor passes the unit's
`ConditionKernelModuleLoaded`). Every operation is `check=False` and
idempotent; a guest unreachable from the worker is logged and skipped: the
failure handler must never itself fail. Note the mirror duty in
`f/runtime_tests/start`, which does the same unload defensively before each
start, so a passed exit-honest module stays loaded only until the next run or
this stop.

Force-stopping a Windmill job (SIGKILL of the worker process) bypasses
`failure_module`, so it does not reach here; the manual fallback is
`systemctl --host <vm> stop modprobe@<module>.service`.

Equivalent commands, against the guest over vsock-SSH:

    ssh <vm> systemctl stop         modprobe@<module>.service
    ssh <vm> systemctl reset-failed modprobe@<module>.service
    ssh <vm> modprobe --remove --quiet <module>
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.remote import RemoteSystemd
from f.common.remote import list_vms as _list_vms
from f.runtime_tests.common import unit_for


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, modules: list[str] | None = None) -> dict:
    modules = list(modules or [])
    if not vm_name or not modules:
        print(
            f"+ stop: nothing to do (vm_name={vm_name!r}, modules={modules})",
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
    for module in modules:
        try:
            unit = unit_for(module)
        except ValueError as exc:
            print(f"{vm_name}: skipping {module!r} ({exc})", flush=True)
            continue
        remote.systemctl("stop", unit, check=False)
        remote.systemctl("reset-failed", unit, check=False)
        remote.ssh("modprobe", "--remove", "--quiet", module, check=False)
        stopped.append(unit)
    return {"vm_name": vm_name, "stopped": stopped, "skipped_no_transport": False}
