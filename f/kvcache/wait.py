# SPDX-License-Identifier: copyleft-next-0.3.1
"""Wait for one replay to finish on a booted guest, with crash detection.

Polls `kvcache@<preset>.service` on the guest (over vsock-SSH) until its
`ActiveState` settles to `inactive` (the `Type=oneshot` unit's success terminus)
or `failed` with no start job left queued, or the timeout elapses: while the
server downloads and loads the model, the replay is `inactive` with its start
job waiting. The outcome is read from `Result` and
`ExecMainStatus` (kvcache-bench exits 0 once it wrote a summary). A server
that dies mid-replay takes `vllm-ready.service` down with it (BindsTo=), and
the replay, which Requires= that gate, gets a stop job: it ends without
`Result=success` rather than hanging, and f/kvcache/collect calls it failed. A
server that fails before it answers drops the replay's queued start job
instead: the unit stays `inactive`, never having run, so the wait compares its
`InactiveExitTimestampMonotonic` with the value f/kvcache/start read before the
start, and an unchanged (or never set) one reports the replay as never run,
with the server's and its readiness gate's state as the reason and their
journal tails in the job log.

Each poll also checks the HOST `qemu-system@<vm>.service`: any not-alive state
means the guest is gone and the wait stops with `crashed=True` rather than
burning the timeout on a dead transport, and so does a run of consecutive
failed polls with QEMU still up (a guest whose sshd died). Each poll streams the
guest's merged unit + kernel journal into the job log (`stream_logs`); the
replay prints one line per failed session and its summary at the end. Without
streaming, a bounded tail is dumped for triage. A replay that overran the
timeout is stopped, not left running.

Equivalent commands:

    # guest, over vsock-SSH, each poll:
    systemctl --host <vm> show kvcache@<preset>.service \
        --property=Result --property=ExecMainStatus --property=ActiveState \
        --property=Job --property=InactiveExitTimestampMonotonic
    # host systemd --user, each poll (crash check):
    systemctl --user is-active qemu-system@<vm>.service
    # guest journal tail on completion (stream_logs=False opt-out):
    ssh <vm> journalctl --no-pager --output=short-precise --lines 200 \
        _SYSTEMD_UNIT=kvcache@<preset>.service
    ssh <vm> journalctl --no-pager --output=short-precise --lines 200 \
        _TRANSPORT=kernel
    # guest, when the replay never ran, per server unit (vllm-serve.service,
    # then vllm-ready.service):
    systemctl --host <vm> show <unit> --property=ActiveState --property=Result
    ssh <vm> journalctl --no-pager --output=short-precise --lines 200 \
        _SYSTEMD_UNIT=<unit>
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from f.common.devshell import Systemd
from f.kvcache.common import RemoteSystemd
from f.kvcache.common import list_vms as _list_vms

_DONE = ("inactive", "failed")
_ALIVE = ("active", "activating", "reloading", "refreshing")
_SERVER_UNITS = ("vllm-serve.service", "vllm-ready.service")

# Consecutive failed polls before a transport-dead guest (QEMU alive, SSH gone)
# counts as crashed; at the default 15 s interval this is five minutes.
_TRANSPORT_BUDGET = 20
_JOURNAL_LINES = 200


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def decide(
    host_state: str, state: dict[str, str] | None, poll_errors: int, overdue: bool
) -> str:
    """One poll's outcome: `crashed`, `done`, `timeout`, or `poll` to keep going.

    `state` is the unit's properties, or None when the guest poll failed, in
    which case `poll_errors` counts that failure too.
    """
    if host_state not in _ALIVE:
        return "crashed"
    if state is None and poll_errors >= _TRANSPORT_BUDGET:
        return "crashed"
    if state is not None and state.get("ActiveState") in _DONE and not state.get("Job"):
        return "done"
    return "timeout" if overdue else "poll"


def never_ran(state: dict[str, str], inactive_exit: str = "") -> bool:
    """A settled unit never left `inactive` since start read `inactive_exit`."""
    now = state.get("InactiveExitTimestampMonotonic", "")
    return now in ("", "0") or (bool(inactive_exit) and now == inactive_exit)


def server_reason(servers: dict[str, dict[str, str]]) -> str:
    """Why the replay never ran, from the server units' ActiveState and Result."""
    states = ", ".join(
        f"{unit} {p.get('ActiveState') or '?'} (Result={p.get('Result') or '?'})"
        for unit, p in servers.items()
    )
    return f"the replay never ran: the server did not come up ({states})"


def main(
    vm_name: str,
    preset: str,
    timeout: int = 86400,
    poll_interval: int = 15,
    stream_logs: bool = True,
    inactive_exit: str = "",
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    host = Systemd(workers)
    unit = f"kvcache@{preset}.service"
    qemu_unit = f"qemu-system@{vm_name}.service"

    props = (
        "Result",
        "ExecMainStatus",
        "ActiveState",
        "Job",
        "InactiveExitTimestampMonotonic",
    )
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

        # A long run's guest can be too busy under test load to answer the vsock-SSH
        # poll within the connect timeout (ssh exits 255). That is not the run failing
        # (the host QEMU crash-check is the authority on a dead guest), so a
        # transient poll error just retries; only the deadline, a real crash, or an
        # exhausted transport budget ends the wait. The budget covers the guest that
        # dies with its qemu still alive (an OOM storm that takes sshd, a panic without
        # reboot): a whole run of consecutive transport failures is a dead guest, not a
        # blip.
        polled: dict[str, str] | None = None
        if host_state in _ALIVE:
            try:
                polled = remote.show(unit, *props)
            except Exception as exc:
                poll_errors += 1
                print(
                    f"{vm_name}: poll of {unit} failed ({exc}); QEMU still up, "
                    f"retrying (consecutive errors: {poll_errors})",
                    flush=True,
                )
            else:
                poll_errors = 0
                state = polled
                drain_logs()
                active_state = state.get("ActiveState", "")

        outcome = decide(host_state, polled, poll_errors, time.monotonic() >= deadline)
        if outcome == "crashed":
            crashed = True
            # Any not-alive state is a gone guest: `failed` is a crash, and
            # `inactive` a clean outside stop of the VM.
            if host_state not in _ALIVE:
                print(
                    f"{vm_name}: {qemu_unit} is {host_state or 'gone'}: guest is "
                    f"down, stopping poll",
                    flush=True,
                )
            else:
                print(
                    f"{vm_name}: transport dead for {poll_errors} consecutive polls "
                    f"with QEMU still up; treating the guest as crashed",
                    flush=True,
                )
            break
        if outcome == "timeout":
            timed_out = True
            last = "last poll errored" if polled is None else active_state
            print(f"{vm_name}: timed out after {timeout}s ({last})", flush=True)
            break
        if outcome == "done":
            break
        time.sleep(int(poll_interval))

    ended_realtime_ms = int(time.time() * 1000)

    # A replay that overran its poll deadline is still running in the guest
    # (TimeoutStartSec=infinity), so abort it rather than leave it spinning.
    if timed_out:
        print(f"{vm_name}: stopping {unit} after the timeout", flush=True)
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

    reason = ""
    ran = True
    if not (crashed or timed_out) and never_ran(state, inactive_exit):
        ran = False
        servers: dict[str, dict[str, str]] = {}
        for server in _SERVER_UNITS:
            try:
                servers[server] = remote.show(server, "ActiveState", "Result")
            except Exception as exc:
                print(f"{vm_name}: reading {server} failed ({exc})", flush=True)
                servers[server] = {}
            tail = remote.ssh(
                "journalctl",
                "--no-pager",
                "--output=short-precise",
                "--lines",
                str(_JOURNAL_LINES),
                f"_SYSTEMD_UNIT={server}",
                check=False,
            )
            if tail:
                print(f"--- {server} (last {_JOURNAL_LINES}) ---\n{tail}", flush=True)
        reason = server_reason(servers)
        print(f"{vm_name}: {reason}", flush=True)

    result = state.get("Result", "")
    exec_status = state.get("ExecMainStatus", "")
    print(
        f"{vm_name}: {unit} finished result={result!r} exec_status={exec_status!r} "
        f"active_state={active_state!r} crashed={crashed} timed_out={timed_out}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "preset": preset,
        "result": result,
        "exec_status": exec_status,
        "active_state": active_state,
        "crashed": crashed,
        "timed_out": timed_out,
        "never_ran": not ran,
        "reason": reason,
        "started_realtime_ms": started_realtime_ms,
        "ended_realtime_ms": ended_realtime_ms,
    }
