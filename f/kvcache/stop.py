# SPDX-License-Identifier: copyleft-next-0.3.1
"""Stop a replay (and optionally the engine) on a booted guest over vsock-SSH.

Stops `kvcache-bench@<preset>.service`; with `stop_engine`, also
`vllm-serve.service` (which takes `vllm-ready.service` down with it via its
Requires= binding), releasing the GPU memory. Stopping a Type=oneshot
mid-run lands Result=canceled/killed, which `wait` reports faithfully.

Equivalent commands:

    systemctl --host <vm> stop kvcache-bench@<preset>.service
    systemctl --host <vm> stop vllm-serve.service   # stop_engine
"""

from __future__ import annotations

import os
from pathlib import Path

from f.kvcache.common import RemoteSystemd, _safe_preset
from f.kvcache.common import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, preset: str, stop_engine: bool = False) -> dict:
    remote = RemoteSystemd(Path(os.environ["WORKERS_DIR"]), vm_name)
    preset = _safe_preset(preset)
    stopped = [f"kvcache-bench@{preset}.service"]
    remote.systemctl("stop", stopped[0])
    if stop_engine:
        remote.systemctl("stop", "vllm-serve.service")
        stopped.append("vllm-serve.service")
    print(f"{vm_name}: stopped {', '.join(stopped)}", flush=True)
    return {"vm": vm_name, "stopped": stopped}
