# SPDX-License-Identifier: copyleft-next-0.3.1
"""Start one KUnit suite on a booted guest over vsock-SSH (fire-and-forget).

Captures the guest journal's end-of-now cursor, then starts the suite's unit with
`--no-block`: `kunit@<suite>.service` for a re-runnable suite (it writes the
suite's debugfs `run` node, then reads `results` back to its journal), or
`kunit-results@<suite>.service` for an init-only suite (no `run` node; only its
boot-time results can be read). The cursor is the run's identity: everything
after it in the unit's journal belongs to this run and nothing before it does,
however fast the unit finishes or whether systemd still has the dead instance
loaded, so `f/kunit/wait` and `f/kunit/collect` can never confuse this run with
a previous one. The unit's exit codes carry no pass/fail verdict (its last
command is a `cat`); the verdict is the KTAP the journal captures.

Equivalent commands, against the guest over vsock-SSH:

    ssh <vm> journalctl --boot --lines=0 --show-cursor
    systemctl --host <vm> start --no-block kunit@<suite>.service
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.remote import RemoteSystemd
from f.common.remote import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, suite: str, runnable: bool = True) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)
    template = "kunit" if runnable else "kunit-results"
    unit = f"{template}@{suite}.service"

    cursor = remote.journal_cursor()
    remote.systemctl("start", "--no-block", unit)

    print(f"{vm_name}: started {unit} (journal cursor captured first)", flush=True)
    return {
        "vm": vm_name,
        "suite": suite,
        "unit": unit,
        "cursor": cursor,
    }
