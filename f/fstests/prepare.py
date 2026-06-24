# SPDX-License-Identifier: copyleft-next-0.3.1
"""Prepare a booted guest for one xfstests section over vsock-SSH.

Activates the section's `<section>.config` as `local.config` (the unit's
`HOST_OPTIONS`), then on the guest (re)creates the xfstests mount points, loads the
section's filesystem driver, and formats `TEST_DEV` with the section's `FSTYP`. The
`FSTYP` is orchestration data read from the rendered config, not baked in. For an xfs
section it then captures the realized `xfs_info` of the formatted device to
`<share>/<vm>/<section>.xfs_info`, so the report can show the actual feature set
(reflink, rmapbt, bigtime, crc, ...) mkfs enabled beyond `MKFS_OPTIONS`.

Equivalent commands (config activation host-side, the rest against the guest):

    cp <section>.config local.config
    ssh <vm> modprobe <FSTYP>
    ssh <vm> mkdir --parents <TEST_DIR> <SCRATCH_MNT>
    ssh <vm> umount <TEST_DEV>
    ssh <vm> mkfs --type <FSTYP> <force> <MKFS_OPTIONS...> <TEST_DEV>
    ssh <vm> xfs_info <TEST_DEV>          # xfs only; saved as <section>.xfs_info
"""

from __future__ import annotations

import os
from pathlib import Path

from f.fstests.common import (
    RemoteSystemd,
    _atomic_write,
    section_vars,
    share_dir,
)
from f.fstests.common import (
    list_vms as _list_vms,
)

# Per-FSTYP `mkfs` overwrite flag, so a re-run does not refuse an already-formatted
# TEST_DEV. xfs/btrfs/f2fs use `-f`; the ext family uses `-F` (mke2fs). An FSTYP not
# listed here gets no force flag; mkfs decides.
MKFS_FORCE_FLAG = {
    "xfs": "-f",
    "btrfs": "-f",
    "f2fs": "-f",
    "ext2": "-F",
    "ext3": "-F",
    "ext4": "-F",
}


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.fstests.common.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, section: str, mkfs_test_dev: bool = True) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    share = share_dir(vm_name)
    config = share / f"{section}.config"
    if not config.is_file():
        raise RuntimeError(
            f"{vm_name}: no rendered config at {config}; run f/fstests/render_config first"
        )
    config_text = config.read_text()
    vars_ = section_vars(config_text, section)

    # Activate this section as the unit's HOST_OPTIONS (local.config): one section
    # per config, so check resolves its FSTYP without multi-section interference.
    local = share / "local.config"
    _atomic_write(local, config_text)
    print(f"+ wrote {local} ([{section}])", flush=True)

    fstyp = vars_.get("FSTYP", "")
    test_dev = vars_.get("TEST_DEV", "")
    if not fstyp or not test_dev:
        raise RuntimeError(
            f"{vm_name}: section [{section}] is missing "
            f"{'FSTYP' if not fstyp else 'TEST_DEV'} in {config}"
        )

    test_dir = vars_.get("TEST_DIR", "")
    scratch_mnt = vars_.get("SCRATCH_MNT", "")
    mkfs_options = vars_.get("MKFS_OPTIONS", "")

    remote = RemoteSystemd(workers, vm_name)

    # best-effort; a built-in/loaded FSTYP is a no-op
    print(f"+ modprobe {fstyp}", flush=True)
    remote.ssh("modprobe", fstyp, check=False)

    mnts = [m for m in (test_dir, scratch_mnt) if m]
    if mnts:
        print(f"+ mkdir --parents {' '.join(mnts)}", flush=True)
        remote.ssh("mkdir", "--parents", *mnts)

    # clear any stale mount before mkfs
    print(f"+ umount {test_dev}", flush=True)
    remote.ssh("umount", test_dev, check=False)

    formatted = False
    if mkfs_test_dev:
        argv = ["mkfs", "--type", fstyp]
        force = MKFS_FORCE_FLAG.get(fstyp)
        if force:
            argv.append(force)
        if mkfs_options:
            argv += mkfs_options.split()
        argv.append(test_dev)
        print(f"+ {' '.join(argv)}", flush=True)
        remote.ssh(*argv)
        formatted = True

    # Capture the realized filesystem geometry/feature set of the just-formatted device,
    # so the report shows what mkfs actually enabled (reflink, rmapbt, bigtime, crc, ...)
    # beyond the configured MKFS_OPTIONS. `xfs_info` reads the unmounted device read-only;
    # best-effort and xfs-only (the report degrades to the configured geometry without it).
    xfs_info = ""
    if fstyp == "xfs":
        print(f"+ xfs_info {test_dev}", flush=True)
        xfs_info = (
            remote.ssh("xfs_info", test_dev, check=False, quiet=True) or ""
        ).strip()
        if xfs_info:
            xi_path = share / f"{section}.xfs_info"
            _atomic_write(xi_path, xfs_info + "\n")
            print(f"+ wrote {xi_path}", flush=True)

    print(
        f"{vm_name}: prepared [{section}] fstyp={fstyp} test_dev={test_dev} "
        f"formatted={formatted}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "section": section,
        "fstyp": fstyp,
        "test_dev": test_dev,
        "test_dir": test_dir,
        "scratch_mnt": scratch_mnt,
        "formatted": formatted,
        "mkfs_options": mkfs_options,
        "xfs_info": xfs_info,
    }
