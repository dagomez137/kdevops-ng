# SPDX-License-Identifier: copyleft-next-0.3.1
"""Discover a booted guest's xfstests readiness over vsock-SSH (read-only).

Checks the guest is up and xfstests-ready (`xfstests@.service` template + `./check`
runner present), then enumerates the NVMe data disks (`/dev/nvme*n1`) and supported
filesystems. Two NVMe disks are the minimum (TEST + SCRATCH); fewer than four is
surfaced for a btrfs pool, never hard-failed. Mutates nothing on the guest.

Equivalent commands, against the guest over vsock-SSH:

    systemctl --host <vm> is-system-running
    systemctl --host <vm> cat xfstests@.service
    ssh <vm> test -x /usr/lib/xfstests/check
    ssh <vm> lsblk --nodeps --noheadings --output NAME,SIZE,TYPE,LOG-SEC
    ssh <vm> cat /proc/filesystems
"""

from __future__ import annotations

import os
from pathlib import Path

from f.fstests.common import RemoteSystemd, list_vms as _list_vms

GUEST_CHECK = "/usr/lib/xfstests/check"


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.fstests.common.list_vms`."""
    return _list_vms(filterText)


def _devices(remote: RemoteSystemd) -> list[dict]:
    """The guest's NVMe data disks (`/dev/nvme*n1`) as `{name, size, log_sec}`, file order.

    Lists whole disks (no partitions); keeps the `disk`-type `nvme*n1` namespaces,
    the ones a `local.config` maps to TEST/SCRATCH. `log_sec` is the device's
    logical sector size in bytes (lsblk `LOG-SEC`; the minimum filesystem block
    size `mkfs.xfs` enforces), defaulting to 512 when lsblk omits a parseable value.
    """
    out = remote.ssh(
        "lsblk", "--nodeps", "--noheadings", "--output", "NAME,SIZE,TYPE,LOG-SEC"
    ) or ""
    devices: list[dict] = []
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        name, size, kind = fields[0], fields[1], fields[2]
        log_sec = int(fields[3]) if len(fields) >= 4 and fields[3].isdigit() else 512
        if kind == "disk" and name.startswith("nvme") and name.endswith("n1"):
            devices.append({"name": f"/dev/{name}", "size": size, "log_sec": log_sec})
    return devices


def _fstyp_supported(remote: RemoteSystemd) -> list[str]:
    """Filesystems the guest kernel supports, from `/proc/filesystems`.

    Each line is `[nodev]\\t<name>`; we keep the on-disk filesystems' names (the
    `FSTYP` values a section can target).
    """
    out = remote.ssh("cat", "/proc/filesystems") or ""
    return [parts[-1] for line in out.splitlines() if (parts := line.split())]


def _kernel_release(remote: RemoteSystemd) -> str:
    """The guest's running kernel release (`uname -r`), from `/proc/sys/kernel/osrelease`.

    This is the same value the systemd `%v` specifier resolves to in the
    `xfstests@.service` unit, so the host keys results under the identical
    `<kver>` the guest writes to. Read at discover time, before the optional
    `reboot` step: sound under the same-closure-same-kernel assumption (a plain
    reboot boots the same default kernel); a reboot that switched the default
    kernel would desync the host's key from the guest's `%v`.
    """
    out = (remote.ssh("cat", "/proc/sys/kernel/osrelease") or "").strip()
    if not out:
        raise RuntimeError("could not read /proc/sys/kernel/osrelease (uname -r) from guest")
    return out


def main(vm_name: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)

    system_state = remote.is_system_running()
    booted = system_state in ("running", "degraded")
    if not booted:
        raise RuntimeError(
            f"{vm_name}: guest not booted (is-system-running={system_state!r}); "
            f"boot it with f/qsu/boot before running xfstests"
        )

    unit_present = remote.unit_exists("xfstests@.service")
    check_present = remote.ssh("test", "-x", GUEST_CHECK, capture=False, check=False) == 0
    fstests_ready = unit_present and check_present
    if not fstests_ready:
        raise RuntimeError(
            f"{vm_name}: not xfstests-ready (xfstests@.service "
            f"{'present' if unit_present else 'missing'}, {GUEST_CHECK} "
            f"{'present' if check_present else 'missing'})"
        )

    devices = _devices(remote)
    if len(devices) < 2:
        raise RuntimeError(
            f"{vm_name}: need >= 2 NVMe data disks for TEST + SCRATCH, found {len(devices)}"
        )
    if len(devices) < 4:
        print(f"note: {len(devices)} NVMe disks; a btrfs pool wants >= 4", flush=True)

    fstyp = _fstyp_supported(remote)
    kernel_version = _kernel_release(remote)
    print(f"{vm_name}: booted={system_state} fstests_ready=True "
          f"devices={len(devices)} fstyp={len(fstyp)} supported "
          f"kernel={kernel_version}", flush=True)
    return {
        "vm": vm_name,
        "host": system_state,
        "booted": booted,
        "fstests_ready": fstests_ready,
        "devices": devices,
        "fstyp_supported": fstyp,
        "kernel_version": kernel_version,
    }
