# SPDX-License-Identifier: copyleft-next-0.3.1
"""Wait for one KUnit suite to finish on a booted guest, with crash detection.

Polls the guest's journal for the unit `f/kunit/start` started, reading only
entries past start's cursor, so everything seen belongs to this run. Completion
is systemd's own job-outcome record in that stream: PID1 logs exactly one of
`MSG_UNIT_STARTED` ("Finished <unit>", the start job succeeded) or
`MSG_UNIT_FAILED` ("Failed to start <unit>") per start job, and `MSG_UNIT_STOPPED`
when an outside stop ended it. This holds even when the sub-second oneshot
instance is already deactivated and garbage-collected before the first poll,
where `systemctl show` would read back defaults. The same records carry the run's
KTAP (the unit's own output) and the unit's process exits (`EXIT_STATUS`), so the
returned `ktap` is the run-scoped document `f/kunit/collect` parses, and
`exec_status` carries a failing command's exit status when there is one
(systemd journal-logs successful process exits only at debug level, so a clean
run leaves it empty; the "Finished" outcome already proves every command
exited 0). The pass/fail verdict is the KTAP itself.

A KUnit suite is sub-second, so the poll deadline only matters for a hang or an
oops. Each poll also checks the host `qemu-system@<vm>.service`: any not-alive
state (`failed`, or `inactive` after a clean outside stop) means the guest is
gone and the wait ends with `crashed=True` rather than burning the timeout on a
dead transport.

Equivalent commands:

    # guest, over vsock-SSH, each poll:
    ssh <vm> journalctl --output=json --after-cursor=<cursor> \
        --unit=kunit@<suite>.service
    # host systemd --user, each poll (liveness check):
    systemctl --user is-active qemu-system@<vm>.service
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from f.common.devshell import Systemd
from f.common.remote import (
    MSG_UNIT_FAILED,
    MSG_UNIT_PROCESS_EXIT,
    MSG_UNIT_STARTED,
    MSG_UNIT_STARTING,
    MSG_UNIT_STOPPED,
    RemoteSystemd,
    journal_message,
)
from f.common.remote import list_vms as _list_vms

_ALIVE = ("active", "activating", "reloading", "refreshing")
_TAIL_LINES = 40


def _monotonic_us(rec: dict) -> int | None:
    """The record's `__MONOTONIC_TIMESTAMP` (microseconds), when it parses."""
    try:
        return int(rec["__MONOTONIC_TIMESTAMP"])
    except (KeyError, TypeError, ValueError):
        return None


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(
    vm_name: str,
    suite: str,
    unit: str,
    cursor: str,
    timeout: int = 600,
    poll_interval: int = 5,
    stream_logs: bool = True,
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    host = Systemd(workers)
    qemu_unit = f"qemu-system@{vm_name}.service"

    deadline = time.monotonic() + int(timeout)
    result = ""
    exec_status = ""
    crashed = False
    timed_out = False
    poll_errors = 0
    started_at: int | None = None
    ended_at: int | None = None
    ktap_lines: list[str] = []
    # Stream the display journal from the run's own cursor, not from boot: a
    # boot-anchored first drain would dump the guest's entire dmesg into the
    # job log in one oversized chunk (which the job-log pipeline drops).
    log_cursor: str | None = cursor

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

    def scan(records: list[dict]) -> None:
        """Fold this run's new journal records into the outcome and the KTAP."""
        nonlocal result, exec_status, started_at, ended_at
        for rec in records:
            if rec.get("_SYSTEMD_UNIT") == unit:
                ktap_lines.append(journal_message(rec))
            mid = rec.get("MESSAGE_ID", "")
            if mid == MSG_UNIT_STARTING:
                if started_at is None:
                    started_at = _monotonic_us(rec)
            elif mid == MSG_UNIT_PROCESS_EXIT:
                exec_status = str(rec.get("EXIT_STATUS", ""))
            elif mid == MSG_UNIT_STARTED:
                result = "done"
                ended_at = _monotonic_us(rec)
            elif mid == MSG_UNIT_FAILED:
                result = "failed"
                ended_at = _monotonic_us(rec)
            elif mid == MSG_UNIT_STOPPED and not result:
                result = "stopped"
                ended_at = _monotonic_us(rec)

    while True:
        host_state = (
            host.systemctl("is-active", qemu_unit, capture=True, check=False) or ""
        ).strip()
        if host_state not in _ALIVE:
            print(
                f"{vm_name}: {qemu_unit} is {host_state or 'gone'}: guest is down, "
                f"stopping poll",
                flush=True,
            )
            crashed = True
            break

        # A transient vsock-SSH poll failure is not the run failing (the host qemu
        # liveness check above is the authority on a dead guest), so it just
        # retries; only the deadline (or the guest going down) ends the wait.
        try:
            cursor, records = remote.journal_unit(unit, cursor)
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
        scan(records)
        drain_logs()
        if result:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            print(f"{vm_name}: timed out after {timeout}s (no job outcome)", flush=True)
            break
        time.sleep(int(poll_interval))

    # A suite that overran its poll deadline is hung in the guest (an oops or a
    # never-returning test); abort it rather than leave it spinning.
    if timed_out:
        print(f"{vm_name}: stopping {unit} after the suite timeout", flush=True)
        remote.systemctl("stop", unit, check=False)
        try:
            cursor, records = remote.journal_unit(unit, cursor)
            scan(records)
        except Exception:
            pass

    if stream_logs:
        drain_logs()
    elif ktap_lines:
        # No live stream was requested; still leave a bounded tail in the job log
        # so a failure is diagnosable without reaching for the guest.
        tail = ktap_lines[-_TAIL_LINES:]
        print(f"{vm_name}: {unit} journal tail ({len(tail)} lines):", flush=True)
        print("\n".join(tail), flush=True)

    runtime = (
        round((ended_at - started_at) / 1e6, 2)
        if started_at is not None and ended_at is not None
        else None
    )
    print(
        f"{vm_name}: {unit} finished result={result!r} exec_status={exec_status!r} "
        f"crashed={crashed} timed_out={timed_out} runtime={runtime}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "suite": suite,
        "unit": unit,
        "result": result,
        "exec_status": exec_status,
        "ktap": "\n".join(ktap_lines),
        "crashed": crashed,
        "timed_out": timed_out,
        "runtime": runtime,
    }
