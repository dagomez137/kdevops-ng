# SPDX-License-Identifier: copyleft-next-0.3.1
"""Discover a booted guest's runtime-tests readiness over vsock-SSH (read-only).

Checks the guest is up and carries upstream systemd's `modprobe@.service`
template (the suite's executor: one instance per test module, its start job
synchronizing on module init), reads the running kernel release, then derives
which catalog modules the booted kernel actually shipped from one read of its
`modules.dep` (one batch over the transport, no per-module round trips): a
module is present when a `.ko` basename in the dep index matches its name,
with the kernel's `-`/`_` equivalence normalized. A kernel shipping none of
the catalog fails here: a run would silently test nothing. Writes the present
list to the per-VM picker cache; mutates nothing on the guest.

Equivalent commands:

    systemctl --host <vm> is-system-running
    systemctl --host <vm> list-unit-files modprobe@.service
    ssh <vm> cat /proc/sys/kernel/osrelease
    ssh <vm> cat /lib/modules/<kver>/modules.dep
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from f.common.remote import RemoteSystemd
from f.common.remote import list_vms as _list_vms
from f.runtime_tests.common import CATALOG, _atomic_write, modules_cache


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def _kernel_release(remote: RemoteSystemd) -> str:
    """The guest's running kernel release (`uname -r`), from `/proc/sys/kernel/osrelease`."""
    out = (remote.ssh("cat", "/proc/sys/kernel/osrelease") or "").strip()
    if not out:
        raise RuntimeError(
            "could not read /proc/sys/kernel/osrelease (uname -r) from guest"
        )
    return out


def _dep_modules(dep_text: str) -> set[str]:
    """The module names a `modules.dep` ships: each line's `.ko` path basename
    before the first `:`, extension (and any compression suffix) stripped, `-`
    normalized to `_` as the kernel's module loader does."""
    names: set[str] = set()
    for line in dep_text.splitlines():
        path = line.split(":", 1)[0].strip()
        base = path.rsplit("/", 1)[-1]
        if ".ko" not in base:
            continue
        names.add(base.split(".ko", 1)[0].replace("-", "_"))
    return names


def main(vm_name: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)

    system_state = remote.is_system_running()
    booted = system_state in ("running", "degraded")
    if not booted:
        raise RuntimeError(
            f"{vm_name}: guest not booted (is-system-running={system_state!r}); "
            f"boot it with f/qsu/boot before running the runtime tests"
        )

    if not remote.unit_exists("modprobe@.service"):
        raise RuntimeError(
            f"{vm_name}: modprobe@.service template missing; the guest closure "
            f"does not ship systemd's upstream unit set"
        )

    kernel_version = _kernel_release(remote)
    dep_text = remote.ssh("cat", f"/lib/modules/{kernel_version}/modules.dep") or ""
    shipped = _dep_modules(dep_text)
    modules = [m for m in CATALOG if m in shipped]
    missing = [m for m in CATALOG if m not in shipped]
    if not modules:
        raise RuntimeError(
            f"{vm_name}: kernel {kernel_version} ships none of the "
            f"{len(CATALOG)} cataloged runtime-test modules; build it with the "
            f"runtime-tests config fragment (RUNTIME_TESTING_MENU entries =m)"
        )

    cache = modules_cache(vm_name)
    _atomic_write(cache, json.dumps(modules) + "\n")
    print(
        f"+ wrote {cache} ({len(modules)} modules for the run form)",
        flush=True,
    )
    print(
        f"{vm_name}: booted={system_state} kernel={kernel_version} "
        f"modules={len(modules)}/{len(CATALOG)} "
        f"missing={','.join(missing) or 'none'}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "host": system_state,
        "booted": booted,
        "kernel_version": kernel_version,
        "modules": modules,
        "missing": missing,
    }
