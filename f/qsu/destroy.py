# SPDX-License-Identifier: copyleft-next-0.3.1
"""Destroy QEMU/systemd VMs (ports destroy.yml's per-VM teardown).

Stop each instance, then remove every per-VM artefact: the `<vm>.env`, the
`qemu-system@<vm>.service.d` drop-in dir, the `virtiofsd@<vm>-*.service.d` drop-in
dirs and `virtiofsd@<vm>-*.env` files, the systemd `StateDirectory`
(`~/.local/state/qemu-system/<vm>`: the NVMe qcow2 backing files + runtime sockets),
the `shared/vm/<vm>.vars.json` reuse sidecar (so a destroyed VM stops appearing in the
`f/qsu/bringup` Reuse-from-VM dropdown, which globs that registry), and the
`$SYSTEM_DIR/ssh/config.d/<vm>.conf` alias `f/qsu/boot` wrote (so `ssh <vm>` stops resolving
once the guest is gone).
machined unregisters automatically on stop. A final `daemon-reload` drops the removed
drop-ins.

When the selection leaves NO rendered `<vm>.env` behind, the host-wide artefacts
shared across VMs are torn down too, matching the qsu manual's
"Remove everything": the virtiofsd listening sockets are stopped (they socket-activate
new virtiofsd processes until stopped, since `qemu-system@<vm>.service` does not pin
them), the host-wide template units (`qemu-system@.service`, `virtiofsd@.service`,
`virtiofsd@.socket`, `vfio-bind@.service`) are removed, and the `qemu-system`/`virtiofsd`
config dirs (the static `qmp-powerdown` and any stragglers) are swept. `f/qsu/boot`
re-renders all of these unconditionally on the next deploy, so the slate is clean now and
self-heals on first boot. While other VMs remain the shared files stay in place.

Equivalent commands, against the host `systemd --user` manager (plus the per-VM
artefact removals), once per selected VM:

    systemctl --user stop qemu-system@<vm>.service
    systemctl --user daemon-reload

and, only once no VM is left, additionally (qsu manual "Remove everything"):

    systemctl --user stop 'virtiofsd@*.socket'
    rm --recursive --force \\
      ~/.config/systemd/user/qemu-system@.service \\
      ~/.config/systemd/user/virtiofsd@.service \\
      ~/.config/systemd/user/virtiofsd@.socket \\
      ~/.config/systemd/user/vfio-bind@.service \\
      ~/.config/systemd/qemu-system \\
      ~/.config/systemd/virtiofsd
    systemctl --user daemon-reload
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from f.common.devshell import Systemd, system_dir
from f.qsu.common import state_dir, systemd_config, vm_options


def _rm(path: Path) -> str | None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)
    else:
        return None
    return str(path)


def _teardown_shared(systemd: Systemd, cfg: Path) -> list[str]:
    """Remove the host-wide artefacts shared across VMs (qsu "Remove everything").

    Called only when the destroyed VM was the last one. The virtiofsd listening
    sockets are not pinned by `qemu-system@<vm>.service`, so they keep
    socket-activating fresh virtiofsd processes until stopped explicitly; systemctl
    expands the unit glob itself (the runner passes argv straight to execve, no shell).
    """
    systemd.systemctl("stop", "virtiofsd@*.socket", check=False)
    user = cfg / "user"
    targets = [
        user / "qemu-system@.service",
        user / "virtiofsd@.service",
        user / "virtiofsd@.socket",
        user / "vfio-bind@.service",
        cfg / "qemu-system",
        cfg / "virtiofsd",
    ]
    return [r for r in (_rm(p) for p in targets) if r]


def _targets(cfg: Path, workers: Path, vm_name: str) -> list[Path]:
    """Every per-VM artefact of `vm_name`, in removal order."""
    user = cfg / "user"
    return [
        cfg / "qemu-system" / f"{vm_name}.env",
        user / f"qemu-system@{vm_name}.service.d",
        state_dir(vm_name),
        workers / "shared/vm" / f"{vm_name}.vars.json",
        system_dir() / "ssh/config.d" / f"{vm_name}.conf",
        *user.glob(f"virtiofsd@{vm_name}-*.service.d"),
        *(cfg / "virtiofsd").glob(f"{vm_name}-*.env"),
    ]


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_vms` entrypoint: see `f.qsu.common.vm_options`."""
    return vm_options(filterText)


def main(vm_names: list[str]) -> dict:
    names = list(dict.fromkeys(vm_names or []))
    if not names:
        raise ValueError("pick at least one VM to destroy")

    workers = Path(os.environ["WORKERS_DIR"])
    systemd = Systemd(workers)
    cfg = systemd_config()

    destroyed = []
    removed: list[str] = []
    for vm_name in names:
        rc = systemd.systemctl("stop", f"qemu-system@{vm_name}.service", check=False)
        paths = [r for r in (_rm(p) for p in _targets(cfg, workers, vm_name)) if r]
        for r in paths:
            print(f"removed {r}", flush=True)
        destroyed.append({"vm_name": vm_name, "stopped": rc == 0, "removed": paths})
        removed += paths

    # vm_options enumerates VMs by rendered `<vm>.env` (union with live machines), so no
    # remaining env means the selection took the last VM with it and the shared host-wide
    # files are orphaned.
    shared_torn_down = not any((cfg / "qemu-system").glob("*.env"))
    if shared_torn_down:
        shared = _teardown_shared(systemd, cfg)
        for r in shared:
            print(f"removed {r}", flush=True)
        removed += shared

    systemd.systemctl("daemon-reload", check=False)
    return {
        "vm_names": names,
        "destroyed": destroyed,
        "removed": removed,
        "shared_torn_down": shared_torn_down,
    }
