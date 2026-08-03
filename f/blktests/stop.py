# SPDX-License-Identifier: copyleft-next-0.3.1
"""Stop blktests@<group>.service unit(s) and lingering per-test scopes on a
booted guest over vsock-SSH.

Wired as `check.flow`'s `failure_module` so cancelling a run from the Windmill
UI, or any step erroring out mid-group, tears the running unit down on the
guest instead of leaving `./check <group>` burning CPU under
`TimeoutStartSec=infinity`. Per-group units are independent, so the helper
iterates each group the run was driving and stops them all; then it stops any
remaining `blktests-*.scope` unit: the per-test transient scopes live OUTSIDE
the service cgroup, so stopping the service alone can orphan an in-flight
scoped test. The stops are idempotent (an inactive unit is a no-op once
`reset-failed` clears any latched state, including a scope failed by its
watchdog). A guest unreachable from the worker is logged and skipped; the
failure handler must not itself fail.

Force-stopping a Windmill job (SIGKILL of the worker process) bypasses
`failure_module`, so it does not reach here; the manual fallback is
`systemctl --host <vm> stop blktests@<group>.service`.

Equivalent commands, against the guest over vsock-SSH:

    ssh <vm> systemctl stop         blktests@<group>.service
    ssh <vm> systemctl reset-failed blktests@<group>.service
    ssh <vm> systemctl list-units --type=scope --all --no-legend --plain 'blktests-*'
    ssh <vm> systemctl stop         <scope>
    ssh <vm> systemctl reset-failed <scope>
"""

from __future__ import annotations

import os
from pathlib import Path

from f.blktests.common import RemoteSystemd
from f.blktests.common import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def list_groups(filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_groups` entrypoint: the static group catalog.

    A run can drive groups discover enumerated beyond the catalog; the field
    still accepts them via the flow, this picker only offers the catalog.
    """
    from f.blktests.common import GROUPS

    needle = (filterText or "").lower()
    return [
        {"value": g["name"], "label": g["name"]}
        for g in GROUPS
        if needle in g["name"].lower()
    ]


def main(vm_name: str, groups: list[str] | None = None) -> dict:
    groups = list(groups or [])
    if not vm_name or not groups:
        print(
            f"+ stop: nothing to do (vm_name={vm_name!r}, groups={groups})",
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
    for group in groups:
        unit = f"blktests@{group}.service"
        remote.systemctl("stop", unit, check=False)
        remote.systemctl("reset-failed", unit, check=False)
        stopped.append(unit)
    out = (
        remote.systemctl(
            "list-units",
            "--type=scope",
            "--all",
            "--no-legend",
            "--plain",
            "blktests-*",
            capture=True,
            check=False,
        )
        or ""
    )
    scopes = [
        fields[0]
        for line in out.splitlines()
        if (fields := line.split()) and fields[0].endswith(".scope")
    ]
    for scope in scopes:
        remote.systemctl("stop", scope, check=False)
        remote.systemctl("reset-failed", scope, check=False)
        stopped.append(scope)
    return {"vm_name": vm_name, "stopped": stopped, "skipped_no_transport": False}
