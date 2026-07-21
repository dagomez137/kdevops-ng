# SPDX-License-Identifier: copyleft-next-0.3.1
"""Prepare a booted guest for one xfstests section over vsock-SSH.

Activates the section's `<section>.config` as `local.config` (the unit's
`HOST_OPTIONS`), creates the xfstests mount points, and loads the section's
filesystem driver. It does NOT format or mount any device: xfstests owns that.
`./check` reformats `SCRATCH_DEV` before every test, mounts and unmounts both
`TEST_DEV` and `SCRATCH_DEV` itself (`init_rc` mounts `TEST_DEV` with its
`-o rtdev=`), and reformats `TEST_DEV` per section when `RECREATE_TEST_DEV=true`
(the default, set in `check.env` by `f/fstests/render_config`), attaching the
section's realtime/external-log device via its own `_test_mkfs`. The external
volumes are never separately formatted; they are attached to the data fs.

`f/fstests/wait` captures each device's realized `xfs_info` at run-end (once
`./check` has formatted them), so the report shows what the run built. This step
only reports the section's configured device layout for reference.

Equivalent commands (config activation host-side, the rest against the guest):

    cp <section>.config local.config
    ssh <vm> modprobe <FSTYP>
    ssh <vm> mkdir --parents <TEST_DIR> <SCRATCH_MNT>
"""

from __future__ import annotations

import os
from pathlib import Path

from f.fstests.common import (
    RemoteSystemd,
    _atomic_write,
    section_device_map,
    section_vars,
    share_dir,
)
from f.fstests.common import (
    list_vms as _list_vms,
)


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.fstests.common.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, section: str) -> dict:
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
    use_external = vars_.get("USE_EXTERNAL", "") == "yes"
    devices = section_device_map(config_text, section)

    remote = RemoteSystemd(workers, vm_name)

    # best-effort; a built-in/loaded FSTYP is a no-op
    print(f"+ modprobe {fstyp}", flush=True)
    remote.ssh("modprobe", fstyp, check=False)

    # The mount points must exist before check mounts TEST_DEV/SCRATCH_DEV onto them.
    mnts = [m for m in (test_dir, scratch_mnt) if m]
    if mnts:
        print(f"+ mkdir --parents {' '.join(mnts)}", flush=True)
        remote.ssh("mkdir", "--parents", *mnts)

    print(
        f"{vm_name}: prepared [{section}] fstyp={fstyp} use_external={use_external} "
        f"devices={devices}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "section": section,
        "fstyp": fstyp,
        "test_dir": test_dir,
        "scratch_mnt": scratch_mnt,
        "use_external": use_external,
        "devices": devices,
    }
