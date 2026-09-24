# SPDX-License-Identifier: copyleft-next-0.3.1
"""Start one KV cache replay preset on a booted guest over vsock-SSH.

Removes the preset's previous `summary.json` from the host side of the share
first (the replayer writes it only at run end; a leftover from an earlier run
of the same preset on the same kernel would otherwise stand in for a run that
crashed, and read as a false pass). Then starts
`kvcache-bench@<preset>.service` with `--no-block`: the unit is
`Type=oneshot` and a full replay runs for hours, so a blocking start would
not return. The unit `Requires=vllm-ready.service`, which in turn requires
`vllm-serve.service`, so systemd brings the engine up and gates on its
/health response before the replay begins -- there is no separate
engine-start step and no readiness polling here. `f/kvcache/wait` polls for
the outcome. After starting, `ActiveState` is read back and asserted
`activating`/`active`, so a start that never took fails here rather than
silently in the wait step.

Equivalent commands:

    # host side of the share (stale-result removal):
    rm --force .../<vm>/<kver>/results/<preset>/summary.json
    # against the guest over vsock-SSH:
    systemctl --host <vm> start --no-block \\
        kvcache-bench@<preset>.service
    systemctl --host <vm> show \\
        kvcache-bench@<preset>.service --property=ActiveState
"""

from __future__ import annotations

import os
from pathlib import Path

from f.kvcache.common import RemoteSystemd, _safe_preset, results_dir
from f.kvcache.common import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, preset: str, kernel_version: str = "") -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    preset = _safe_preset(preset)
    unit = f"kvcache-bench@{preset}.service"

    if kernel_version:
        stale = results_dir(vm_name, kernel_version, preset) / "summary.json"
        if stale.is_file():
            stale.unlink()
            print(f"+ removed stale {stale}", flush=True)

    remote.systemctl("start", "--no-block", unit)
    active_state = remote.show(unit, "ActiveState").get("ActiveState", "")
    if active_state not in ("activating", "active"):
        raise RuntimeError(
            f"{vm_name}: {unit} did not start (ActiveState={active_state!r}, "
            f"expected activating/active)"
        )
    print(f"{vm_name}: started {unit} (ActiveState={active_state})", flush=True)
    return {"vm": vm_name, "preset": preset, "unit": unit, "active_state": active_state}
