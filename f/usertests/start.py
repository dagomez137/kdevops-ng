# SPDX-License-Identifier: copyleft-next-0.3.1
"""Start one usertests harness on a booted guest over vsock-SSH (fire-and-forget).

Captures the guest journal's end-of-now cursor, then starts the item's
`usertests@<instance>.service` with `--no-block`; the instance is the
systemd-escaped `<dir>/<binary>` item (`radix-tree/main` ->
`radix\\x2dtree-main`). The cursor is the run's identity: everything after it
in the unit's journal belongs to this run and nothing before it does, so
`f/usertests/wait` and `f/usertests/collect` can never confuse this run with a
previous one. The template's `ExecStart` carries no `-` prefix, so the
harness's exit code is REAL: a `done` job outcome proves it exited 0, and an
assert or sanitizer abort surfaces as a failed job with `EXIT_STATUS`
populated.

Equivalent commands, against the guest over vsock-SSH:

    ssh <vm> journalctl --boot --lines=0 --show-cursor
    systemctl --host <vm> start --no-block usertests@<instance>.service
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.remote import RemoteSystemd
from f.common.remote import list_vms as _list_vms
from f.usertests.common import item_unit


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, item: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    unit = item_unit(item)

    cursor = remote.journal_cursor()
    remote.systemctl("start", "--no-block", unit)

    print(f"{vm_name}: started {unit} (journal cursor captured first)", flush=True)
    return {
        "vm": vm_name,
        "item": item,
        "unit": unit,
        "cursor": cursor,
    }
