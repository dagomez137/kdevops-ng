# SPDX-License-Identifier: copyleft-next-0.3.1
"""Wait for one runtime-test module load to finish on a booted guest, with crash detection.

Polls the guest's journal for the `modprobe@<module>.service` instance
`f/runtime_tests/start` started, reading only entries past start's cursor, so
everything seen belongs to this run. Completion is systemd's own job-outcome
record in that stream: PID1 logs exactly one of `MSG_UNIT_STARTED` ("Finished
<unit>"), `MSG_UNIT_FAILED`, or `MSG_UNIT_SKIPPED` (the unit's
`ConditionKernelModuleLoaded=!%i` found the module already loaded, so nothing
ran) per start job, and `MSG_UNIT_STOPPED` when an outside stop ended it. The
unit's `ExecStart` carries systemd's `-` prefix, so a failed modprobe still
finishes the job `done`, AND the prefix makes the process exit expected,
which systemd logs only at debug level: the init's exit status is
structurally unobservable here (live-run confirmed: both classes come back
`done` with an empty `exec_status`, which is returned for forensics only).
The observable init outcome is the module's post-run load state, so after
the job outcome (and after a timeout abort) one extra probe checks
`/sys/module/<module>` and returns it as `loaded`; together with the kmsg
lines it carries the verdict.

The suite's results live in the kernel ring buffer, not the unit's output: a
module's summary printk lines and any WARNING/BUG splat carry no
`_SYSTEMD_UNIT`, so `journal_unit`'s `--unit=` match cannot see them, and
`journal_combined` returns formatted display text without per-record fields.
Each poll therefore adds a second cursor-anchored JSON fetch on
`_TRANSPORT=kernel`, and the collected lines are returned as `kmsg`, the
verdict channel `f/runtime_tests/collect` scans and parses.

Module init is synchronous and unbounded by the unit (`Type=oneshot`), so this
poll deadline is the only bound on the run. Each poll also checks the host
`qemu-system@<vm>.service`: any not-alive state (a crash-on-fail module like
atomic64_test BUGs the guest) ends the wait as `crashed` rather than burning
the timeout on a dead transport.

Equivalent commands:

    # guest, over vsock-SSH, each poll:
    ssh <vm> journalctl --output=json --after-cursor=<cursor> \
        --unit=modprobe@<module>.service
    ssh <vm> journalctl --output=json --after-cursor=<cursor> _TRANSPORT=kernel
    # host systemd --user, each poll (liveness check):
    systemctl --user is-active qemu-system@<vm>.service
    # guest, once, after the job outcome:
    ssh <vm> test -e /sys/module/<module>
"""

from __future__ import annotations

import json
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
from f.runtime_tests.common import MSG_UNIT_SKIPPED

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


def _journal_kernel(
    remote: RemoteSystemd, cursor: str | None
) -> tuple[str | None, list[dict]]:
    """This boot's `_TRANSPORT=kernel` journal records past `cursor`, parsed.

    The kernel-transport twin of `f.common.remote.journal_unit`, with its own
    cursor: printk output (a module's summary lines, WARN/BUG splats) rides the
    kernel transport and carries no `_SYSTEMD_UNIT`, so the unit fetch cannot
    see it. Raises on a failed fetch, like `journal_unit`.
    """
    args = [
        "journalctl",
        "--no-pager",
        "--output=json",
        "--show-cursor",
        "_TRANSPORT=kernel",
    ]
    args += [f"--after-cursor={cursor}"] if cursor else ["--boot"]
    out = remote.ssh(*args, quiet=True) or ""
    next_cursor, records = cursor, []
    for line in out.splitlines():
        if line.startswith("-- cursor:"):
            next_cursor = line.split(":", 1)[1].strip()
            continue
        if not line.startswith("{"):
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return next_cursor, records


def main(
    vm_name: str,
    module: str,
    unit: str,
    cursor: str,
    timeout: int = 600,
    poll_interval: int = 10,
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
    kmsg_lines: list[str] = []
    run_cursor: str | None = cursor
    kmsg_cursor: str | None = cursor
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
        """Fold this run's new unit journal records into the job outcome."""
        nonlocal result, exec_status, started_at, ended_at
        for rec in records:
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
            elif mid == MSG_UNIT_SKIPPED:
                result = "skipped"
                ended_at = _monotonic_us(rec)
            elif mid == MSG_UNIT_STOPPED and not result:
                result = "stopped"
                ended_at = _monotonic_us(rec)

    def fetch() -> None:
        """One poll: the unit's records for the outcome, the kernel's for the verdict."""
        nonlocal run_cursor, kmsg_cursor
        run_cursor, records = remote.journal_unit(unit, run_cursor)
        scan(records)
        kmsg_cursor, krecords = _journal_kernel(remote, kmsg_cursor)
        kmsg_lines.extend(journal_message(r) for r in krecords)

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
            fetch()
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
        if result:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            print(f"{vm_name}: timed out after {timeout}s (no job outcome)", flush=True)
            break
        time.sleep(int(poll_interval))

    # A run that overran its poll deadline is hung in the guest (a wedged
    # module init); abort it rather than leave it spinning.
    if timed_out:
        print(f"{vm_name}: stopping {unit} after the run timeout", flush=True)
        remote.systemctl("stop", unit, check=False)
        try:
            fetch()
        except Exception:
            pass

    # The post-run load state is the observable init outcome (the exit status
    # is not); a dead guest is unreachable and collect fails on crashed anyway.
    loaded = False
    if not crashed:
        try:
            rc = remote.ssh(
                "test", "-e", f"/sys/module/{module}", capture=False, check=False
            )
            loaded = rc == 0
        except Exception as exc:
            print(f"{vm_name}: load-state probe failed ({exc})", flush=True)

    if stream_logs:
        drain_logs()
    elif kmsg_lines:
        # No live stream was requested; still leave a bounded tail in the job log
        # so a failure is diagnosable without reaching for the guest.
        tail = kmsg_lines[-_TAIL_LINES:]
        print(f"{vm_name}: {unit} kmsg tail ({len(tail)} lines):", flush=True)
        print("\n".join(tail), flush=True)

    runtime = (
        round((ended_at - started_at) / 1e6, 2)
        if started_at is not None and ended_at is not None
        else None
    )
    print(
        f"{vm_name}: {unit} finished result={result!r} loaded={loaded} "
        f"exec_status={exec_status!r} crashed={crashed} timed_out={timed_out} "
        f"runtime={runtime} kmsg_lines={len(kmsg_lines)}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "module": module,
        "unit": unit,
        "result": result,
        "loaded": loaded,
        "exec_status": exec_status,
        "kmsg": "\n".join(kmsg_lines),
        "crashed": crashed,
        "timed_out": timed_out,
        "runtime": runtime,
    }
