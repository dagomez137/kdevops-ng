# SPDX-License-Identifier: copyleft-next-0.3.1
"""Start one runtime-test module on a booted guest over vsock-SSH (fire-and-forget).

Clears the previous instance, captures the guest journal's end-of-now cursor,
then starts upstream systemd's `modprobe@<module>.service` with `--no-block`;
its start job synchronizes on the module's init, which runs the whole suite.
The cursor is the run's identity: everything after it in the journal belongs
to this run and nothing before it does, so `f/runtime_tests/wait` and
`f/runtime_tests/collect` can never confuse this run with a previous one.

The run-identity trap this step defuses: a module still loaded from a previous
run does not re-run its tests. `modprobe` of a loaded module is a successful
no-op, and the unit's own `ConditionKernelModuleLoaded=!%i` skips the start
job outright, so without cleanup a stale pass (or a silent skip) would wear
this run's cursor. Hence, before starting: `systemctl reset-failed` (an
expected-failure class instance latches a failed state that would otherwise
confuse a re-run) and, for the stay-loaded exit-honest class, a defensive
`modprobe --remove --quiet` so the load genuinely re-runs the suite. Both are
`check=False`: nothing to clean is the common case.

Equivalent commands, against the guest over vsock-SSH:

    ssh <vm> systemctl reset-failed modprobe@<module>.service
    ssh <vm> modprobe --remove --quiet <module>        # unload class only
    ssh <vm> journalctl --boot --lines=0 --show-cursor
    systemctl --host <vm> start --no-block modprobe@<module>.service
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.remote import RemoteSystemd
from f.common.remote import list_vms as _list_vms
from f.runtime_tests.common import catalog_entry, unit_for


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, module: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    unit = unit_for(module)
    entry = catalog_entry(module)

    remote.systemctl("reset-failed", unit, check=False)
    if entry["unload"]:
        remote.ssh("modprobe", "--remove", "--quiet", module, check=False)

    cursor = remote.journal_cursor()
    remote.systemctl("start", "--no-block", unit)

    print(f"{vm_name}: started {unit} (journal cursor captured first)", flush=True)
    return {
        "vm": vm_name,
        "module": module,
        "unit": unit,
        "cursor": cursor,
    }
