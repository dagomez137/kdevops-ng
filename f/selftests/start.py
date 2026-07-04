# SPDX-License-Identifier: copyleft-next-0.3.1
"""Start one kselftest run item on a booted guest over vsock-SSH (fire-and-forget).

Captures the guest journal's end-of-now cursor, then starts the item's unit with
`--no-block`: `kselftest@<instance>.service` for a whole collection
(`run_kselftest.sh --collection`), or `kselftest-test@<instance>.service` for a
single COLLECTION:TEST list entry (`run_kselftest.sh --test`); the instance is
the systemd-escaped item name (`net/forwarding` -> `net-forwarding`,
`cpu-hotplug` -> `cpu\\x2dhotplug`). The cursor is the run's identity:
everything after it in the unit's journal belongs to this run and nothing
before it does, so `f/selftests/wait` and `f/selftests/collect` can never
confuse this run with a previous one. The unit's exit codes carry no pass/fail
verdict (the templates pass `--no-error-on-fail`, so a nonzero exit means an
infrastructure error, never a test failure); the verdict is the KTAP the
journal captures.

Equivalent commands, against the guest over vsock-SSH:

    ssh <vm> journalctl --boot --lines=0 --show-cursor
    systemctl --host <vm> start --no-block kselftest@<instance>.service
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.remote import RemoteSystemd
from f.common.remote import list_vms as _list_vms
from f.selftests.common import item_unit


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
