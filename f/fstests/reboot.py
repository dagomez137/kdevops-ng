# SPDX-License-Identifier: copyleft-next-0.3.1
"""Cold power-cycle a guest's QEMU/systemd unit from the host and wait for it to come back.

A run-global startup step: restart the host `qemu-system@<vm>.service` unit and block
until a fresh guest boot reaches `running`/`degraded`, so a run starts from clean kernel
state: no leaked state, stuck D-state tasks, or dirty page cache from a prior run. Off by
default in the check flow. The device wipe is a SEPARATE step (`f/fstests/wipe`); run this
before it so the wipe acts on a fresh boot.

A HOST cold power-cycle, not a guest `systemctl reboot`: the host restart QMP-powerdowns
the old guest then cold-starts a fresh QEMU process (SIGKILL on the unit's `TimeoutStopSec`),
so it can NOT hang on a wedged guest (exactly the state this step exists to clear), and it
fully resets device/kernel state, where a warm in-guest reboot would not. The qcow2 disks,
vsock CID, ssh alias, and virtiofsd all persist across the restart, so the guest is probed
over vsock afterward by the same boot_id poll as before.

Equivalent command, against the host `systemd --user` manager:

    systemctl --user restart qemu-system@<vm>.service   # then poll the guest's boot_id over vsock until a fresh boot reaches running/degraded
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from f.common.devshell import Systemd
from f.fstests.common import RemoteSystemd, list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.fstests.common.list_vms`."""
    return _list_vms(filterText)


def _boot_id(remote: RemoteSystemd) -> str:
    """The guest's current boot id, or `""` if unreachable (mid-reboot, or already wedged)."""
    return (remote.ssh("cat", "/proc/sys/kernel/random/boot_id", check=False, quiet=True) or "").strip()


def _reboot(
    remote: RemoteSystemd,
    systemd: Systemd,
    vm_name: str,
    timeout: int,
    poll_interval: int,
) -> dict:
    """Host cold power-cycle the QEMU unit, then block until a *new* guest boot is up.

    Records the boot id first (best-effort); it may be `""` if the guest is already
    wedged/down, which is fine: a host restart kills the old QEMU process and starts a
    fresh one unconditionally, so any non-empty new boot_id is a genuine fresh boot. The
    `systemctl restart` blocks until the unit's powerdown + cold start finishes (and on
    failure raises, propagated). No `daemon-reload` here: a reboot re-renders nothing, so
    reloading the manager would be a pointless reconfigure (that is `f/qsu/boot`'s job).

    Then polls the boot id over vsock. The system-state probe is only made once the boot
    id has actually changed, so the unreachable window costs one ssh round-trip per poll,
    not two.
    """
    before = _boot_id(remote)
    unit = f"qemu-system@{vm_name}.service"
    # systemctl() logs the exact restart invocation itself; only record the boot id
    # we're leaving, so the fresh-boot change reads clearly in the same log.
    print(f"{vm_name}: boot_id before reboot = {before or '?'}", flush=True)
    systemd.systemctl("restart", unit)
    deadline = time.monotonic() + timeout
    now = ""
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        now = _boot_id(remote)
        if not now:
            print("  …guest unreachable (booting)", flush=True)
            continue
        if now != before:
            state = remote.is_system_running(quiet=True)
            if state in ("running", "degraded"):
                print(f"+ rebooted: boot_id={now} is-system-running={state}", flush=True)
                return {"rebooted": True, "boot_id": now, "system_state": state}
            print(f"  …new boot {now} coming up (state={state or '?'})", flush=True)
        else:
            print(f"  …guest reachable on prior boot {now} (waiting for fresh boot)", flush=True)
    raise TimeoutError(
        f"{vm_name}: guest did not return within {timeout}s after host power-cycle "
        f"(last boot_id={now or '?'})"
    )


def main(
    vm_name: str,
    reboot_guest: bool = False,
    reboot_timeout: int = 300,
    poll_interval: int = 5,
) -> dict:
    if not reboot_guest:
        print(f"{vm_name}: reboot skipped (reboot_guest=False)", flush=True)
        return {"vm": vm_name, "rebooted": False}
    if reboot_timeout <= 0 or poll_interval <= 0:
        raise ValueError(
            f"reboot_timeout and poll_interval must be > 0 "
            f"(got {reboot_timeout=}, {poll_interval=})"
        )

    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    systemd = Systemd(workers)
    result: dict = {"vm": vm_name, "rebooted": False}
    result.update(_reboot(remote, systemd, vm_name, reboot_timeout, poll_interval))
    print(f"{vm_name}: reboot done rebooted={result['rebooted']}", flush=True)
    return result
