# SPDX-License-Identifier: copyleft-next-0.3.1
"""Discover a booted guest's KUnit readiness over vsock-SSH (read-only).

Checks the guest is up and KUnit-ready (the `kunit@.service` template installed and
the `/sys/kernel/debug/kunit/` debugfs root mounted), enumerates the available
suites (the debugfs directory entries) and which of them are re-runnable (carry a
`run` node; an init-only suite has none and only its boot-time results can be
read), and reads the running kernel release. A guest exposing zero suites fails
here: the kernel has KUnit but built no tests, so a run would silently test
nothing. Mutates nothing on the guest.

Equivalent commands, against the guest over vsock-SSH:

    systemctl --host <vm> is-system-running
    systemctl --host <vm> list-unit-files kunit@.service
    ssh <vm> test -d /sys/kernel/debug/kunit
    ssh <vm> ls /sys/kernel/debug/kunit
    ssh <vm> sh -c 'ls --directory /sys/kernel/debug/kunit/*/run'
    ssh <vm> cat /proc/sys/kernel/osrelease
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from f.common.remote import RemoteSystemd
from f.common.remote import list_vms as _list_vms
from f.kunit.common import DEBUGFS_DIR, _atomic_write, suites_cache


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def _suites(remote: RemoteSystemd) -> list[str]:
    """The guest's KUnit suites: the `/sys/kernel/debug/kunit/` directory entries."""
    out = remote.ssh("ls", DEBUGFS_DIR) or ""
    return sorted(s for s in out.split() if s)


def _runnable(remote: RemoteSystemd) -> list[str]:
    """The suites carrying a `run` node (re-runnable; init-only suites have none).

    The glob must expand in the guest (the transport quotes argv tokens), so this
    is one `sh -c`; `check=False` because zero matches is a valid answer.
    """
    script = f"ls --directory {DEBUGFS_DIR}/*/run"
    out = remote.ssh("sh", "-c", script, check=False) or ""
    return sorted(
        p.split("/")[-2] for p in out.split() if p.startswith(f"{DEBUGFS_DIR}/")
    )


def _kernel_release(remote: RemoteSystemd) -> str:
    """The guest's running kernel release (`uname -r`), from `/proc/sys/kernel/osrelease`.

    This is the value the systemd `%v` specifier resolves to in the guest's units,
    so the host keys results under the identical `<kver>` the guest reports.
    """
    out = (remote.ssh("cat", "/proc/sys/kernel/osrelease") or "").strip()
    if not out:
        raise RuntimeError(
            "could not read /proc/sys/kernel/osrelease (uname -r) from guest"
        )
    return out


def main(vm_name: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)

    system_state = remote.is_system_running()
    booted = system_state in ("running", "degraded")
    if not booted:
        raise RuntimeError(
            f"{vm_name}: guest not booted (is-system-running={system_state!r}); "
            f"boot it with f/qsu/boot before running KUnit"
        )

    unit_present = remote.unit_exists("kunit@.service")
    debugfs_present = (
        remote.ssh("test", "-d", DEBUGFS_DIR, capture=False, check=False) == 0
    )
    kunit_ready = unit_present and debugfs_present
    if not kunit_ready:
        raise RuntimeError(
            f"{vm_name}: not KUnit-ready (kunit@.service "
            f"{'present' if unit_present else 'missing'}, {DEBUGFS_DIR} "
            f"{'present' if debugfs_present else 'missing'}); build the kernel with "
            f"CONFIG_KUNIT + CONFIG_KUNIT_DEBUGFS and boot it with the `kunit` test "
            f"suite so the suites appear under {DEBUGFS_DIR}"
        )

    suites = _suites(remote)
    if not suites:
        raise RuntimeError(
            f"{vm_name}: {DEBUGFS_DIR} is empty: the kernel has KUnit but built no "
            f"test suites; enable them in the kernel config (built-in, or as "
            f"modules, e.g. CONFIG_KUNIT_ALL_TESTS=m; the guest declares and "
            f"loads the booted kernel's test modules at boot automatically)"
        )
    runnable = _runnable(remote)
    cache = suites_cache(vm_name)
    _atomic_write(cache, json.dumps(suites) + "\n")
    print(f"+ wrote {cache} ({len(suites)} suites for the run form)", flush=True)
    kernel_version = _kernel_release(remote)
    print(
        f"{vm_name}: booted={system_state} kunit_ready=True suites={len(suites)} "
        f"runnable={len(runnable)} kernel={kernel_version}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "host": system_state,
        "booted": booted,
        "kunit_ready": kunit_ready,
        "suites": suites,
        "runnable": runnable,
        "kernel_version": kernel_version,
    }
