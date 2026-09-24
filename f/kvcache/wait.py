# SPDX-License-Identifier: copyleft-next-0.3.1
"""Wait for one KV cache replay to finish on a booted guest, with crash detection.

Polls `kvcache-bench@<preset>.service` (over vsock-SSH) until its
`ActiveState` settles to `inactive` (the `Type=oneshot` success terminus) or
`failed`, or the timeout elapses. The outcome is read from `Result` and
`ExecMainStatus` (the replayer's exit code; non-zero includes the
failed-session-rate gate). Because the unit `Requires=` the engine, an
engine crash mid-run fails this unit's dependency chain and lands here as a
`failed` Result rather than a hang.

Each poll also checks the HOST `qemu-system@<vm>.service`: any not-alive
state means the guest is gone and we stop with `crashed=True` rather than
burning the timeout on a dead transport. With `stream_logs` the guest's new
journal for the unit (which also carries vllm-serve lines via the console)
is drained into the job log every poll.

Equivalent commands:

    systemctl --host <vm> show kvcache-bench@<preset>.service \\
        --property=Result --property=ExecMainStatus \\
        --property=ActiveState
    systemctl --user is-active qemu-system@<vm>.service
    ssh <vm> journalctl \\
        -u kvcache-bench@<preset>.service --after-cursor ...
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from f.common.devshell import Systemd
from f.kvcache.common import RemoteSystemd, _safe_preset
from f.kvcache.common import list_vms as _list_vms

_DONE = ("inactive", "failed")


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(
    vm_name: str,
    preset: str,
    timeout: int = 86400,
    poll_interval: int = 15,
    stream_logs: bool = True,
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    host = Systemd(workers)
    preset = _safe_preset(preset)
    unit = f"kvcache-bench@{preset}.service"
    qemu_unit = f"qemu-system@{vm_name}.service"

    deadline = time.monotonic() + int(timeout)
    state: dict[str, str] = {}
    crashed = False
    timed_out = False
    log_cursor: str | None = None

    def drain_logs() -> None:
        nonlocal log_cursor
        if not stream_logs:
            return
        try:
            log_cursor, body = remote.journal_combined(unit, log_cursor)
        except Exception as exc:  # noqa: BLE001 - logs are best-effort
            print(f"{vm_name}: journal fetch failed ({exc}); continuing", flush=True)
            return
        if body.strip():
            print(body, flush=True)

    while True:
        host_state = (
            host.systemctl("is-active", qemu_unit, capture=True, check=False) or ""
        ).strip()
        if host_state not in ("active", "activating"):
            crashed = True
            print(f"{vm_name}: {qemu_unit} is {host_state!r}; guest gone", flush=True)
            break
        try:
            state = remote.show(unit, "Result", "ExecMainStatus", "ActiveState")
        except Exception as exc:  # noqa: BLE001 - transient vsock hiccup
            print(f"{vm_name}: poll failed ({exc}); retrying", flush=True)
            state = {}
        drain_logs()
        if state.get("ActiveState") in _DONE:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            print(
                f"{vm_name}: {unit} still {state.get('ActiveState')!r} after {timeout}s",
                flush=True,
            )
            break
        time.sleep(int(poll_interval))

    drain_logs()
    result = state.get("Result", "")
    exec_status = state.get("ExecMainStatus", "")
    success = (
        not crashed and not timed_out and result == "success" and exec_status == "0"
    )
    print(
        f"{vm_name}: {unit} done (Result={result!r} ExecMainStatus={exec_status!r} "
        f"crashed={crashed} timed_out={timed_out})",
        flush=True,
    )
    return {
        "vm": vm_name,
        "preset": preset,
        "unit": unit,
        "success": success,
        "result": result,
        "exec_main_status": exec_status,
        "crashed": crashed,
        "timed_out": timed_out,
    }
