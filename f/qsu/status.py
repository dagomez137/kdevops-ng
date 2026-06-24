# SPDX-License-Identifier: copyleft-next-0.3.1
"""Report one QEMU/systemd VM's status (ports console.yml's access banner + a liveness probe).

`systemctl --user is-active qemu-system@<vm>` for liveness, a best-effort
`machinectl --user status <vm>` for the machined registration view, and the SSH /
vsock / console access lines. Read-only: probes the host manager, mutates nothing.

Equivalent commands, against the host `systemd --user` manager:

    systemctl --user is-active qemu-system@<vm>.service
    machinectl --user status <vm>
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import Systemd
from f.qsu.common import state_dir, vm_options


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.qsu.common.vm_options`."""
    return vm_options(filterText)


def main(
    vm_name: str,
    ssh_port: int | None = None,
    vsock_cid: int | None = None,
) -> dict:
    systemd = Systemd(Path(os.environ["WORKERS_DIR"]))
    is_active = systemd.systemctl(
        "is-active", f"qemu-system@{vm_name}.service", capture=True, check=False
    ).strip()
    machine = systemd.machinectl("status", vm_name, capture=True, check=False)

    access = {
        "status": f"systemctl --user status qemu-system@{vm_name}",
        "logs": f"journalctl --user-unit=qemu-system@{vm_name}.service",
        "list": "machinectl --user list",
        "console": (
            "socat -,raw,echo=0,escape=0x1d "
            f"UNIX-CONNECT:$XDG_RUNTIME_DIR/qemu-system/{vm_name}/console.sock"
        ),
    }
    if ssh_port:
        access["ssh"] = f"ssh -p {ssh_port} root@127.0.0.1"
    if vsock_cid:
        access["vsock"] = f"ssh root@vsock/{vsock_cid}"

    print(f"qemu-system@{vm_name}: {is_active}", flush=True)
    for line in access.values():
        print(f"  {line}", flush=True)

    return {
        "vm_name": vm_name,
        "active": is_active == "active",
        "is_active": is_active,
        "machined": machine.strip() or None,
        "state_dir": str(state_dir(vm_name)),
        "access": access,
    }
