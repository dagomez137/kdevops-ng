# SPDX-License-Identifier: copyleft-next-0.3.1
"""Stop one QEMU/systemd VM (ports destroy.yml's per-VM `systemctl --user stop`).

`systemctl --user stop qemu-system@<vm>` runs the unit's `ExecStop=` (QMP
`system_powerdown`, falling back to SIGKILL after `TimeoutStopSec=2min`). Best-effort:
a partially-cleaned tree (missing unit file, live cgroup) still stops cleanly, so a
non-zero rc is not fatal. virtiofsd auto-stops via `StopWhenUnneeded=yes` once the
`Requires=` pin drops.

Equivalent command, against the host `systemd --user` manager:

    systemctl --user stop qemu-system@<vm>.service
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import Systemd
from f.qsu.common import vm_options


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.qsu.common.vm_options`."""
    return vm_options(filterText)


def main(vm_name: str) -> dict:
    systemd = Systemd(Path(os.environ["WORKERS_DIR"]))
    rc = systemd.systemctl("stop", f"qemu-system@{vm_name}.service", check=False)
    print(f"stop qemu-system@{vm_name}: rc={rc}", flush=True)
    return {"vm_name": vm_name, "stopped": rc == 0, "rc": rc}
