# SPDX-License-Identifier: copyleft-next-0.3.1
"""Stop a run's replays and the vLLM server on a booted guest over vsock-SSH.

Wired as the run flow's `teardown` once the presets ran, and as its
`failure_module`, so a finished run frees the server's memory and cancelling a
run or any step erroring mid-replay tears the guest side down instead of
leaving a replay spinning under `TimeoutStartSec=infinity` and the server
holding the GPU. Stops each preset's `kvcache@<preset>.service`, then
`vllm-serve.service` (its readiness gate stops with it), then removes the
`disk` backend's offload directory from the share. Idempotent, and a guest the
worker cannot reach is logged and skipped: the failure handler must not itself
fail.

Force-stopping a Windmill job bypasses `failure_module`; the manual fallback is
`systemctl --host <vm> stop 'kvcache@*' vllm-serve.service`.

Equivalent commands, against the guest over vsock-SSH, per unit (each
`kvcache@<preset>.service`, then `vllm-serve.service`):

    systemctl --host <vm> stop <unit>
    systemctl --host <vm> reset-failed <unit>
    rm --recursive --force "$WORKERS_DIR/shared/kvcache/<vm>/lmcache-disk"
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from f.kvcache.common import RemoteSystemd, lmcache_disk_dir
from f.kvcache.common import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, presets: list[str] | None = None) -> dict:
    if not vm_name:
        print("stop: nothing to do (no vm_name)", flush=True)
        return {"vm": vm_name, "stopped": [], "skipped_no_transport": False}
    try:
        remote = RemoteSystemd(Path(os.environ["WORKERS_DIR"]), vm_name)
    except Exception as exc:
        print(f"{vm_name}: cannot reach guest ({exc}); skipping stop", flush=True)
        return {"vm": vm_name, "stopped": [], "skipped_no_transport": True}
    units = [f"kvcache@{p}.service" for p in presets or [] if p]
    units.append("vllm-serve.service")
    for unit in units:
        try:
            remote.systemctl("stop", unit, check=False)
            remote.systemctl("reset-failed", unit, check=False)
        except Exception as exc:
            print(f"{vm_name}: stopping {unit} failed ({exc}); continuing", flush=True)
    try:
        disk = lmcache_disk_dir(vm_name)
        if disk.exists():
            shutil.rmtree(disk)
            print(f"removed {disk}", flush=True)
    except Exception as exc:
        print(f"{vm_name}: clearing the LMCache disk failed ({exc})", flush=True)
    return {"vm": vm_name, "stopped": units, "skipped_no_transport": False}
