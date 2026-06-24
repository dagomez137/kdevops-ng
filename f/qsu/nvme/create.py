# SPDX-License-Identifier: copyleft-next-0.3.1
"""Create the per-VM NVMe qcow2 backing files (ports render-per-vm.yml's qemu-img step).

The qcow2 files live under the VM's systemd `StateDirectory`
(`~/.local/state/qemu-system/<vm>`), the same directory `qemu-system@<vm>.service`
sets `WorkingDirectory=` to, so vm.env's relative `file=nvme<i>.qcow2` paths resolve at
run time. Idempotent: an existing file is left untouched (matches ansible `creates:`).

`qemu-img` comes from the reproducible nixos-flake `qemu`, host-visible in `/nix/store`
from every worker; never a host/distro qemu-img, and never the VM's own `qemu-system`
(a `qemu-build` emulator lives in a per-worker tree this `vm`-tagged step cannot see).
qcow2 is format-stable, so the images open in whatever `qemu-system` boots the VM.

Equivalent command, one per drive:

    <nix-store>/bin/qemu-img create --format qcow2 <state_dir>/nvme0.qcow2 20G
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import run_logged
from f.qsu.binaries import store_out
from f.qsu.common import nvme_drives, state_dir


def main(
    vm_name: str,
    nvme_drive_count: int = 4,
    nvme_drive_size_gb: int = 20,
    nvme_drives_override: list | None = None,
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    qemu_img = Path(store_out("qemu", workers)) / "bin" / "qemu-img"

    drives = nvme_drives_override or nvme_drives(
        {"nvme_drive_count": nvme_drive_count}
    )
    sdir = state_dir(vm_name)
    sdir.mkdir(parents=True, exist_ok=True)
    print(f"state dir: {sdir}", flush=True)

    created, skipped = [], []
    for d in drives:
        # In explicit-namespace mode the backend file + format live on the namespace.
        ns = d.get("namespaces", [{}])[0]
        backing_file = d.get("file") or ns["file"]
        fmt = d.get("format") or ns.get("format") or "qcow2"
        path = sdir / backing_file
        if path.exists():
            print(f"exists, skipping: {path}", flush=True)
            skipped.append(str(path))
            continue
        run_logged([str(qemu_img), "create", "--format", str(fmt),
                    str(path), f"{nvme_drive_size_gb}G"])
        print(f"created {path}", flush=True)
        created.append(str(path))

    return {
        "vm_name": vm_name,
        "state_dir": str(sdir),
        "qemu_img": str(qemu_img),
        "created": created,
        "skipped": skipped,
    }
