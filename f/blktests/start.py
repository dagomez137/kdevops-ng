# SPDX-License-Identifier: copyleft-next-0.3.1
"""Start one blktests group on a booted guest over vsock-SSH (fire-and-forget).

Removes the group's previous result subtrees from the host side of the share
first: `./check` writes one TSV file per test under `results/<devdir>/<group>`
and a failed `group_requires` writes ZERO files while exiting 0, so a leftover
tree from an earlier run of the same group on the same kernel would otherwise
stand in for a run that skipped or never started, and read as a false pass.
After the removal, anything present under any `results/*/<group>` provably
belongs to this run, and zero files afterwards is a `notrun`.

Then starts `blktests@<group>.service` on the guest with `--no-block`: the
unit is `Type=oneshot`, so a blocking `start` would not return until the whole
group's `./check` run finished (hours). `--no-block` returns immediately;
`f/blktests/wait` polls for the outcome. The `ActiveState` read-back is
informational only: a small group (or an all-skip one) can finish between the
start and the read-back, so a terminal state here is a legitimate
already-finished run, never a start failure. A start that does not take at
all fails the `systemctl start` call itself; the verdict belongs to wait and
to the result files collect reads.

Equivalent commands:

    rm --recursive --force "$WORKERS_DIR/shared/blktests/<vm>/<kver>/results/"*/<group>
    # against the guest over vsock-SSH:
    systemctl --host <vm> start --no-block blktests@<group>.service
    systemctl --host <vm> show blktests@<group>.service --property=ActiveState
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from f.blktests.common import RemoteSystemd, _safe_group, results_dir
from f.blktests.common import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, group: str, kernel_version: str = "") -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    group = _safe_group(group)
    unit = f"blktests@{group}.service"

    if kernel_version:
        results_root = results_dir(vm_name, kernel_version)
        if results_root.is_dir():
            for devdir in sorted(p for p in results_root.iterdir() if p.is_dir()):
                stale = devdir / group
                if stale.is_dir():
                    shutil.rmtree(stale)
                    print(f"+ removed stale {stale}", flush=True)

    remote.systemctl("start", "--no-block", unit)
    active_state = remote.show(unit, "ActiveState").get("ActiveState", "")
    print(f"{vm_name}: started {unit} (ActiveState={active_state})", flush=True)
    return {
        "vm": vm_name,
        "group": group,
        "unit": unit,
        "active_state": active_state,
    }
