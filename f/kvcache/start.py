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
import time
from pathlib import Path

from f.kvcache.common import _safe_preset, manager, results_dir
from f.kvcache.common import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(
    vm_name: str, preset: str, kernel_version: str = "", target: str = "vm"
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = manager(workers, vm_name, target)
    preset = _safe_preset(preset)
    unit = f"kvcache-bench@{preset}.service"

    if kernel_version:
        stale = (
            results_dir(vm_name, kernel_version, preset, target=target) / "summary.json"
        )
        if stale.is_file():
            stale.unlink()
            print(f"+ removed stale {stale}", flush=True)

    # The invocation baseline: wait distinguishes "this run finished" from
    # "this run never started" by the InvocationID changing from this value
    # (a never-run unit reports systemd's defaults, Result=success and
    # ExecMainStatus=0, which read exactly like a passed run).
    invocation_baseline = remote.show(unit, "InvocationID").get("InvocationID", "")

    remote.systemctl("start", "--no-block", unit)
    # The unit sits behind Requires=vllm-ready -> vllm-serve, so right after a
    # --no-block start its own ActiveState stays `inactive` while systemd runs
    # the dependency jobs (the engine takes minutes to load). A queued start
    # job (the `Job` property) therefore counts as started; only inactive with
    # NO pending job is a start that never took. Poll briefly: each read is a
    # full devShell round trip, so a handful of attempts spans the window in
    # which a genuinely broken unit settles.
    active_state = ""
    for _ in range(10):
        props = remote.show(unit, "ActiveState", "Result", "Job")
        active_state = props.get("ActiveState", "")
        if active_state in ("activating", "active"):
            break
        if props.get("Job"):
            active_state = f"{active_state} (start job {props['Job']} queued)"
            break
        if active_state == "failed":
            break
        time.sleep(2)
    if active_state.startswith("inactive") and "queued" not in active_state:
        raise RuntimeError(
            f"{vm_name}: {unit} did not start (ActiveState={active_state!r}, "
            f"Result={props.get('Result', '')!r}, no pending job); check the "
            f"engine chain: journalctl --user -u vllm-serve.service"
        )
    if active_state == "failed":
        raise RuntimeError(
            f"{vm_name}: {unit} failed at start "
            f"(Result={props.get('Result', '')!r}); a failed dependency "
            f"(vllm-serve/vllm-ready) cancels the run -- read their journals"
        )
    print(f"{vm_name}: started {unit} (ActiveState={active_state})", flush=True)
    return {
        "vm": vm_name,
        "preset": preset,
        "unit": unit,
        "active_state": active_state,
        "invocation_baseline": invocation_baseline,
    }
