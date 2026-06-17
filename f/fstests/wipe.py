# SPDX-License-Identifier: copyleft-next-0.3.1
"""Wipe the test/scratch NVMe devices of a booted guest — runs at the start of each
section (before `f/fstests/prepare` creates the mount points), over vsock-SSH.

`umount` then `wipefs --all` + `blkdiscard --force` every discovered data device.
`blkdiscard` TRIMs each device so the thin-provisioned qcow2 backing deflates;
without it a device left full by a prior section reports `No space left on device`
even after the filesystem is recreated, and the backing inflates across sections.
Running per section before `prepare` trims the devices so each section starts clean.
Safe by default: TEST_DEV/SCRATCH_DEV data is disposable — `f/fstests/prepare`
re-mkfs's TEST_DEV and `./check` re-mkfs's SCRATCH per test.

Equivalent command, per device, over vsock-SSH:

    ssh <vm> 'umount <dev>; wipefs --all <dev>; blkdiscard --force <dev>'
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from f.fstests.common import RemoteSystemd, _device_names, list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name` — see `f.fstests.common.list_vms`."""
    return _list_vms(filterText)


def _wipe(remote: RemoteSystemd, vm_name: str, devices: list[dict]) -> dict:
    """Unmount, clear signatures, and TRIM every data device, in one shell pass.

    `blkdiscard` is the point of the wipe — it TRIMs the device so the qcow2 backing
    deflates — so it must run even when the earlier steps fail. `wipefs` is therefore
    best-effort (a re-run force-formats anyway), and no `set -e`: one device that is
    still busy (a mount `umount` could not clear) must not skip the wipe of the others.
    Each device's discard outcome is reported; the call fails only if *no* device could
    be discarded (a systemic problem, e.g. the transport does not support discard).
    """
    names = _device_names(devices)
    if not names:
        raise ValueError(f"{vm_name}: no devices to wipe (pass devices from f/fstests/discover)")
    devs = " ".join(shlex.quote(n) for n in names)
    script = (
        f"ok=0; for d in {devs}; do "
        'for mp in $(findmnt --raw --noheadings --source "$d" --output TARGET 2>/dev/null); do umount --recursive "$mp" 2>/dev/null || true; done; '
        'umount "$d" 2>/dev/null || true; '
        'wipefs --all "$d" >/dev/null 2>&1 || echo "  warn: wipefs $d failed (continuing)"; '
        'if blkdiscard --force "$d" 2>/dev/null || blkdiscard "$d" 2>/dev/null; then '
        'ok=$((ok+1)); echo "+ wiped $d"; '
        'else echo "  warn: blkdiscard $d failed (busy or no discard support)"; fi; '
        "done; "
        f'echo "wipe: $ok/{len(names)} device(s) discarded"; '
        '[ "$ok" -gt 0 ] || { echo "error: no device could be discarded" >&2; exit 1; }'
    )
    print(f"+ wipe {len(names)} device(s): {' '.join(names)}", flush=True)
    out = remote.ssh("bash", "-c", script, check=True) or ""
    if out:
        print(out, flush=True)
    wiped = [line.removeprefix("+ wiped ") for line in out.splitlines() if line.startswith("+ wiped ")]
    failed = [n for n in names if n not in wiped]
    return {"wiped": wiped, "failed": failed}


def main(
    vm_name: str,
    devices: list[dict] | None = None,
    wipe_devices: bool = True,
) -> dict:
    if not wipe_devices:
        print(f"{vm_name}: wipe skipped (wipe_devices=False)", flush=True)
        return {"vm": vm_name, "wiped": [], "failed": []}

    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    result: dict = {"vm": vm_name}
    result.update(_wipe(remote, vm_name, devices or []))
    print(
        f"{vm_name}: wipe done wiped={len(result['wiped'])} failed={len(result['failed'])} device(s)",
        flush=True,
    )
    return result
