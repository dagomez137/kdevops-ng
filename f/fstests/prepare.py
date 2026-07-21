# SPDX-License-Identifier: copyleft-next-0.3.1
"""Prepare a booted guest for one xfstests section over vsock-SSH.

Activates the section's `<section>.config` as `local.config` (the unit's
`HOST_OPTIONS`), then on the guest (re)creates the xfstests mount points, loads the
section's filesystem driver, and formats `TEST_DEV` with the section's `FSTYP`. The
`FSTYP` is orchestration data read from the rendered config, not baked in. An external
xfs section (realtime/external-log) also attaches its `TEST_RTDEV`/`TEST_LOGDEV` to the
mkfs, so the test device is a realtime/external-log fs and not just the scratch one.
For an xfs section it then captures the realized `xfs_info` of the formatted `TEST_DEV`
to `<share>/<vm>/<section>.xfs_info`, so the report can show the actual feature set
(reflink, rmapbt, bigtime, crc, ...) mkfs enabled beyond `MKFS_OPTIONS`. The result
reports the section's layout per device: each role (`TEST_DEV`, `SCRATCH_DEV`, the
realtime/log volumes, `LOGWRITES_DEV`) carries its device, purpose, and a best-effort
`xfs_info` (a raw external volume has none, which its message confirms).

Equivalent commands (config activation host-side, the rest against the guest):

    cp <section>.config local.config
    ssh <vm> modprobe <FSTYP>
    ssh <vm> mkdir --parents <TEST_DIR> <SCRATCH_MNT>
    ssh <vm> umount <TEST_DEV>
    ssh <vm> mkfs --type <FSTYP> <force> <MKFS_OPTIONS...> [-r rtdev=<TEST_RTDEV>] \
        [-l logdev=<TEST_LOGDEV>] <TEST_DEV>
    ssh <vm> xfs_info <DEV>                # xfs only; TEST_DEV saved as <section>.xfs_info
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

# The section's device roles, reported per device so the step result shows the full
# layout it prepared for, not just TEST_DEV. prepare formats TEST_DEV (with its external
# device); the scratch/logwrites/realtime volumes are xfstests', surfaced for context.
# Order is the report order. The value is the human role label.
DEVICE_ROLES = {
    "TEST_DEV": "test filesystem",
    "SCRATCH_DEV": "scratch filesystem (reformatted by xfstests per test)",
    "SCRATCH_DEV_POOL": "scratch device pool",
    "TEST_RTDEV": "realtime volume for the test filesystem",
    "SCRATCH_RTDEV": "realtime volume for the scratch filesystem",
    "TEST_LOGDEV": "external log for the test filesystem",
    "SCRATCH_LOGDEV": "external log for the scratch filesystem",
    "LOGWRITES_DEV": "dm-log-writes replay log",
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
    mount_options = vars_.get("MOUNT_OPTIONS", "")
    use_external = vars_.get("USE_EXTERNAL", "") == "yes"

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
    mkfs_cmd = ""
    if mkfs_test_dev:
        argv = ["mkfs", "--type", fstyp]
        force = MKFS_FORCE_FLAG.get(fstyp)
        if force:
            argv.append(force)
        if mkfs_options:
            argv += mkfs_options.split()
        # xfstests only mkfs's the scratch device itself (via _scratch_options mkfs);
        # the test device is formatted here, so mirror _test_options mkfs and attach
        # the test-fs external device, else TEST_DEV would be a plain data-only fs with
        # no realtime/external-log section (rtdev=/logdev= on a section that never got
        # one). Gated on USE_EXTERNAL=yes and XFS, as xfstests gates it.
        if fstyp == "xfs" and vars_.get("USE_EXTERNAL") == "yes":
            if test_rtdev := vars_.get("TEST_RTDEV", ""):
                argv += ["-r", f"rtdev={test_rtdev}"]
            if test_logdev := vars_.get("TEST_LOGDEV", ""):
                argv += ["-l", f"logdev={test_logdev}"]
        argv.append(test_dev)
        mkfs_cmd = " ".join(argv)
        print(f"+ {mkfs_cmd}", flush=True)
        remote.ssh(*argv)
        formatted = True
        # Record the realized mkfs command (with any injected external device) so the
        # report shows what actually formatted TEST_DEV, not just the configured
        # MKFS_OPTIONS, which omits the -r rtdev=/-l logdev= attach.
        mk_path = share / f"{section}.mkfs"
        _atomic_write(mk_path, mkfs_cmd + "\n")
        print(f"+ wrote {mk_path}", flush=True)

    # Capture the realized filesystem geometry/feature set of the just-formatted TEST_DEV,
    # so the report shows what mkfs actually enabled (reflink, rmapbt, bigtime, crc, ...)
    # beyond the configured MKFS_OPTIONS. `xfs_info` reads the unmounted device read-only;
    # best-effort and xfs-only (the report degrades to the configured geometry without it).
    # This is the section's fs-under-test, so it is sidecar'd for the run report too.
    test_xfs_info = ""
    if fstyp == "xfs":
        print(f"+ xfs_info {test_dev}", flush=True)
        test_xfs_info = (
            remote.ssh("xfs_info", test_dev, check=False, quiet=True) or ""
        ).strip()
        if test_xfs_info:
            xi_path = share / f"{section}.xfs_info"
            _atomic_write(xi_path, test_xfs_info + "\n")
            print(f"+ wrote {xi_path}", flush=True)

    # Build the per-device report: each role carries its device and purpose. TEST_DEV,
    # the section's fs-under-test and the only device prepare formats, also carries its
    # mount, formatted flag, the realized mkfs command, and its xfs_info. The realtime
    # and log volumes hold no XFS superblock (their role says so), and the scratch fs is
    # (re)formatted by xfstests per test, so only TEST_DEV has a filesystem to query here.
    device_report: dict[str, dict] = {}
    for key, role in DEVICE_ROLES.items():
        dev = vars_.get(key)
        if not dev:
            continue
        entry: dict[str, object] = {"device": dev, "role": role}
        if key == "TEST_DEV":
            entry["mount"] = test_dir
            entry["formatted"] = formatted
            if mkfs_cmd:
                entry["mkfs_cmd"] = mkfs_cmd
            if test_xfs_info:
                entry["xfs_info"] = test_xfs_info
        elif key == "SCRATCH_DEV":
            entry["mount"] = scratch_mnt
        device_report[key] = entry

    dev_paths = {k: v["device"] for k, v in device_report.items()}
    print(
        f"{vm_name}: prepared [{section}] fstyp={fstyp} formatted={formatted} "
        f"use_external={use_external} devices={dev_paths}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "section": section,
        "fstyp": fstyp,
        "use_external": use_external,
        "formatted": formatted,
        "mkfs_options": mkfs_options,
        "mount_options": mount_options,
        "devices": device_report,
    }
