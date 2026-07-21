# SPDX-License-Identifier: copyleft-next-0.3.1
"""Wait for one xfstests section to finish on a booted guest, with crash detection.

Polls `xfstests@<section>.service` on the guest (over vsock-SSH) until its
`ActiveState` settles to `inactive` (the `Type=oneshot` unit's success terminus) or
`failed`, or the timeout elapses. The unit's outcome is read from `Result`
(systemd's enum: `success`/`exit-code`/`signal`/`core-dump`/`timeout`/`watchdog`/
`oom-kill`/...) and `ExecMainStatus` (the `./check` exit code, 0 = all passed).

Each poll also checks the HOST `qemu-system@<vm>.service`: any not-alive state
(`failed` is a crash, `inactive` a clean outside stop) means the guest is gone
and we stop with `crashed=True` rather than burning the timeout on a dead
transport. On completion (or crash) a bounded tail of the guest's unit journal
is dumped to the job log for triage.

Equivalent commands:

    # guest, over vsock-SSH, each poll:
    systemctl --host <vm> show xfstests@<section>.service \
        --property=Result --property=ExecMainStatus --property=ActiveState
    # host systemd --user, each poll (crash check):
    systemctl --user is-active qemu-system@<vm>.service
    # guest journal tail on completion (stream_logs=False opt-out, separate budgets):
    ssh <vm> journalctl --no-pager --lines 200 _SYSTEMD_UNIT=xfstests@<section>.service
    ssh <vm> journalctl --no-pager --lines 200 _TRANSPORT=kernel
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from f.common.devshell import Systemd
from f.fstests.common import (
    RemoteSystemd,
    _atomic_write,
    section_device_map,
    section_vars,
    share_dir,
)
from f.fstests.common import list_vms as _list_vms

_DONE = ("inactive", "failed")
_ALIVE = ("active", "activating", "reloading", "refreshing")
_JOURNAL_LINES = 200


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.fstests.common.list_vms`."""
    return _list_vms(filterText)


def _capture_devices(
    remote: RemoteSystemd, vm_name: str, section: str, workers: Path
) -> dict:
    """Snapshot each device's realized `xfs_info` at run-end, once `./check` has
    formatted them, into `<share>/<vm>/<section>.devices.json`:
    `{ROLE: {"device": path, "xfs_info": text}}`.

    xfs-only, and the pool is skipped (it is a device list, not one device). A raw
    realtime/log volume has no XFS superblock, so its `xfs_info` is the tool's own
    message (`merge_stderr` surfaces it), which confirms it is an external volume.
    """
    cfg = share_dir(vm_name, workers) / f"{section}.config"
    if not cfg.is_file():
        return {}
    text = cfg.read_text()
    fstyp = section_vars(text, section).get("FSTYP", "")
    out: dict[str, dict] = {}
    for role, dev in section_device_map(text, section).items():
        entry: dict[str, str] = {"device": dev}
        if fstyp == "xfs" and role != "SCRATCH_DEV_POOL":
            entry["xfs_info"] = (
                remote.ssh("xfs_info", dev, check=False, quiet=True, merge_stderr=True)
                or ""
            ).strip()
        out[role] = entry
    path = share_dir(vm_name, workers) / f"{section}.devices.json"
    _atomic_write(path, json.dumps(out, indent=2) + "\n")
    print(f"+ wrote {path} ({', '.join(out)})", flush=True)
    return out


def main(
    vm_name: str,
    section: str,
    timeout: int = 86400,
    poll_interval: int = 15,
    stream_logs: bool = True,
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    host = Systemd(workers)
    unit = f"xfstests@{section}.service"
    qemu_unit = f"qemu-system@{vm_name}.service"

    props = ("Result", "ExecMainStatus", "ActiveState")
    # The run window is poll-observed wall clock, not unit-reported timestamps.
    started_realtime_ms = int(time.time() * 1000)
    deadline = time.monotonic() + int(timeout)
    state: dict[str, str] = {}
    active_state = ""
    crashed = False
    timed_out = False
    poll_errors = 0
    log_cursor: str | None = None

    def drain_logs() -> None:
        """Print the guest's new combined unit + kernel journal into the job log."""
        nonlocal log_cursor
        if not stream_logs:
            return
        try:
            log_cursor, body = remote.journal_combined(unit, log_cursor)
        except Exception as exc:
            print(f"{vm_name}: journal fetch failed ({exc}); continuing", flush=True)
            return
        if body.strip():
            print(body, flush=True)

    while True:
        host_state = (
            host.systemctl("is-active", qemu_unit, capture=True, check=False) or ""
        ).strip()
        # Any not-alive state ends the wait: `failed` is a crash, and `inactive`
        # is a clean outside stop of the VM; either way the guest is gone and
        # polling a dead transport until the deadline helps nobody.
        if host_state not in _ALIVE:
            print(
                f"{vm_name}: {qemu_unit} is {host_state or 'gone'}: guest is down, "
                f"stopping poll",
                flush=True,
            )
            crashed = True
            break

        # A long run's guest can be too busy under test load to answer the vsock-SSH
        # poll within the connect timeout (ssh exits 255). That is not the run failing
        # (the host qemu crash-check above is the authority on a dead guest), so a transient
        # poll error just retries; only the deadline (or a real crash) ends the wait.
        try:
            state = remote.show(unit, *props)
        except Exception as exc:
            poll_errors += 1
            print(
                f"{vm_name}: poll of {unit} failed ({exc}); qemu still up, retrying "
                f"(consecutive errors: {poll_errors})",
                flush=True,
            )
            if time.monotonic() >= deadline:
                timed_out = True
                print(
                    f"{vm_name}: timed out after {timeout}s (last poll errored)",
                    flush=True,
                )
                break
            time.sleep(int(poll_interval))
            continue
        poll_errors = 0
        drain_logs()
        active_state = state.get("ActiveState", "")
        if active_state in _DONE:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            print(
                f"{vm_name}: timed out after {timeout}s (ActiveState={active_state})",
                flush=True,
            )
            break
        time.sleep(int(poll_interval))

    ended_realtime_ms = int(time.time() * 1000)

    # A section that overran its poll deadline is still running in the guest
    # (TimeoutStartSec=infinity), so abort it rather than leave it spinning. The
    # per-test watchdog (the unit's check honours TEST_TIMEOUT) handles a single
    # hung test; this bounds the whole section.
    if timed_out:
        print(f"{vm_name}: stopping {unit} after the section timeout", flush=True)
        remote.systemctl("stop", unit, check=False)

    # Final journal, regardless of outcome: the last entries since the previous poll
    # when streaming, else separate bounded unit and kernel tails for triage.
    if stream_logs:
        drain_logs()
    else:
        unit_tail = remote.ssh(
            "journalctl",
            "--no-pager",
            "--output=short-precise",
            "--lines",
            str(_JOURNAL_LINES),
            f"_SYSTEMD_UNIT={unit}",
            check=False,
        )
        if unit_tail:
            print(f"--- {unit} (last {_JOURNAL_LINES}) ---\n{unit_tail}", flush=True)
        kernel_tail = remote.ssh(
            "journalctl",
            "--no-pager",
            "--output=short-precise",
            "--lines",
            str(_JOURNAL_LINES),
            "_TRANSPORT=kernel",
            check=False,
        )
        if kernel_tail:
            print(f"--- kernel (last {_JOURNAL_LINES}) ---\n{kernel_tail}", flush=True)

    # Snapshot each device's realized geometry now that ./check has formatted them
    # (TEST_DEV per RECREATE_TEST_DEV, SCRATCH_DEV per test), for the run report. Skip
    # when the guest crashed (the transport is gone); best-effort otherwise.
    devices: dict = {}
    if not crashed:
        try:
            devices = _capture_devices(remote, vm_name, section, workers)
        except Exception as exc:
            print(
                f"{vm_name}: device geometry capture failed ({exc}); continuing",
                flush=True,
            )

    result = state.get("Result", "")
    exec_status = state.get("ExecMainStatus", "")
    print(
        f"{vm_name}: {unit} finished result={result!r} exec_status={exec_status!r} "
        f"active_state={active_state!r} crashed={crashed} timed_out={timed_out}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "section": section,
        "result": result,
        "exec_status": exec_status,
        "active_state": active_state,
        "crashed": crashed,
        "timed_out": timed_out,
        "started_realtime_ms": started_realtime_ms,
        "ended_realtime_ms": ended_realtime_ms,
        "devices": devices,
    }
