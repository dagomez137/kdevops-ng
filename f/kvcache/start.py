# SPDX-License-Identifier: copyleft-next-0.3.1
"""Start one replay preset on a booted guest over vsock-SSH, detached.

Removes the preset's previous results for this kernel from the host side of
the share first: the replayer writes `summary.json` only at the end, so a
leftover from an earlier run would stand in for a replay that never finished.
Then stops `vllm-serve.service` (its readiness gate `vllm-ready.service` is
bound to it and stops too), so every preset measures from a freshly started
server: vLLM reads its configuration only when it starts, and a running
server would carry its prefix cache and LMCache's CPU cache over from the
previous preset or run. With the server down it removes the `disk` backend's
offload directory from the share, which would otherwise warm the next preset.
It reads the unit's `InactiveExitTimestampMonotonic`, which f/kvcache/wait
compares against to tell a replay that never left `inactive` (its start job
dropped because the server failed to come up) from one that finished. Finally
starts `kvcache@<preset>.service` with
`--no-block`. The unit Requires= `vllm-ready.service`, which is bound to
`vllm-serve.service`, so systemd starts the server and holds the replay until
it answers; a whole replay runs for hours, so f/kvcache/wait polls it. While
the server loads, the unit stays `inactive` with its start job queued
(`Job=`), so a start counts as taken when the unit is active or activating or
has that job; one that did not take fails here, not in the wait.

Equivalent commands:

    rm --recursive --force "$WORKERS_DIR/shared/kvcache/<vm>/<kver>/results/<preset>"
    systemctl --host <vm> stop vllm-serve.service
    rm --recursive --force "$WORKERS_DIR/shared/kvcache/<vm>/lmcache-disk"
    systemctl --host <vm> reset-failed vllm-serve.service vllm-ready.service \
        kvcache@<preset>.service
    systemctl --host <vm> show kvcache@<preset>.service \
        --property=InactiveExitTimestampMonotonic
    systemctl --host <vm> start --no-block kvcache@<preset>.service
    systemctl --host <vm> show kvcache@<preset>.service \
        --property=ActiveState --property=Job
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from f.kvcache.common import RemoteSystemd, lmcache_disk_dir, results_dir
from f.kvcache.common import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def started(props: dict[str, str]) -> bool:
    """A start took: the unit is active or activating, or its start job is
    still queued behind the server's readiness gate."""
    state = props.get("ActiveState", "")
    return state in ("activating", "active") or bool(props.get("Job"))


def _remove(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
        print(f"removed {path}", flush=True)


def main(vm_name: str, preset: str, kernel_version: str) -> dict:
    _remove(results_dir(vm_name, kernel_version, preset))
    remote = RemoteSystemd(Path(os.environ["WORKERS_DIR"]), vm_name)
    unit = f"kvcache@{preset}.service"
    remote.systemctl("stop", "vllm-serve.service")
    _remove(lmcache_disk_dir(vm_name))
    remote.systemctl(
        "reset-failed",
        "vllm-serve.service",
        "vllm-ready.service",
        unit,
        check=False,
    )
    inactive_exit = remote.show(unit, "InactiveExitTimestampMonotonic").get(
        "InactiveExitTimestampMonotonic", ""
    )
    remote.systemctl("start", "--no-block", unit)
    props = remote.show(unit, "ActiveState", "Job")
    state = props.get("ActiveState", "")
    job = props.get("Job", "")
    if not started(props):
        raise RuntimeError(f"{vm_name}: {unit} did not start (ActiveState={state!r})")
    print(f"{vm_name}: started {unit} (ActiveState={state} job={job})", flush=True)
    return {
        "vm": vm_name,
        "preset": preset,
        "unit": unit,
        "active_state": state,
        "inactive_exit": inactive_exit,
    }
