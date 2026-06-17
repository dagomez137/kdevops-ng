# SPDX-License-Identifier: copyleft-next-0.3.1
"""Destroy one QEMU/systemd VM (ports destroy.yml's per-VM teardown).

Stop the instance, then remove every per-VM artefact: the `<vm>.env`, the
`qemu-system@<vm>.service.d` drop-in dir, the `virtiofsd@<vm>-*.service.d` drop-in
dirs and `virtiofsd@<vm>-*.env` files, the systemd `StateDirectory`
(`~/.local/state/qemu-system/<vm>` — the NVMe qcow2 backing files + runtime sockets),
the `shared/vm/<vm>.vars.json` reuse sidecar (so a destroyed VM stops appearing in the
`f/qsu/bringup` Reuse-from-VM dropdown, which globs that registry), and the
`shared/ssh/config.d/<vm>.conf` alias `f/qsu/boot` wrote (so `ssh <vm>` stops resolving
once the guest is gone).
machined unregisters automatically on stop; the host-wide template units are left in
place (they are shared across VMs). A final `daemon-reload` drops the removed drop-ins.

Equivalent commands, against the host `systemd --user` manager (plus the per-VM
artefact removals):

    systemctl --user stop qemu-system@<vm>.service
    systemctl --user daemon-reload
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from f.common.devshell import Systemd
from f.qsu.common import state_dir, systemd_config, vm_options


def _rm(path: Path) -> str | None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)
    else:
        return None
    return str(path)


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name` — see `f.qsu.common.vm_options`."""
    return vm_options(filterText)


def main(vm_name: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    systemd = Systemd(workers)
    rc = systemd.systemctl("stop", f"qemu-system@{vm_name}.service", check=False)

    cfg = systemd_config()
    user = cfg / "user"
    targets = [
        cfg / "qemu-system" / f"{vm_name}.env",
        user / f"qemu-system@{vm_name}.service.d",
        state_dir(vm_name),
        workers / "shared/vm" / f"{vm_name}.vars.json",
        workers / "shared/ssh/config.d" / f"{vm_name}.conf",
        *user.glob(f"virtiofsd@{vm_name}-*.service.d"),
        *(cfg / "virtiofsd").glob(f"{vm_name}-*.env"),
    ]
    removed = [r for r in (_rm(p) for p in targets) if r]
    for r in removed:
        print(f"removed {r}", flush=True)

    systemd.systemctl("daemon-reload", check=False)
    return {"vm_name": vm_name, "stopped": rc == 0, "removed": removed}
