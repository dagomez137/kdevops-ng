# SPDX-License-Identifier: copyleft-next-0.3.1
"""Discover a booted guest's kselftest readiness over vsock-SSH (read-only).

Checks the guest is up and kselftest-ready (the `kselftest@.service` template
installed), reads the running kernel release, and resolves the matching built
kselftest install tree from the local Nix store (the `kselftests-<kver>` index
entry the kernel build publishes). The tree's `kselftest-list.txt` (one
`collection:test` line per test; the collection field can carry `/`, so the
split is on the FIRST `:`) enumerates the collections, in list order. A kernel
with no published tree, or a tree listing zero collections, fails here: a run
would silently test nothing. Writes the collection list to the per-VM picker
cache on the share. Mutates nothing on the guest.

Equivalent commands:

    systemctl --host <vm> is-system-running
    systemctl --host <vm> list-unit-files kselftest@.service
    ssh <vm> cat /proc/sys/kernel/osrelease
    readlink "$SYSTEM_DIR/store-index/kselftests-<kver>"
    cat <store>/kselftest-list.txt
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from f.common import store
from f.common.remote import RemoteSystemd
from f.common.remote import list_vms as _list_vms
from f.selftests.common import _atomic_write, collections_cache, tests_cache


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def _kernel_release(remote: RemoteSystemd) -> str:
    """The guest's running kernel release (`uname -r`), from `/proc/sys/kernel/osrelease`.

    This is the value the systemd `%v` specifier resolves to in the guest's units,
    so the host keys the tree and the results under the identical `<kver>` the
    guest reports.
    """
    out = (remote.ssh("cat", "/proc/sys/kernel/osrelease") or "").strip()
    if not out:
        raise RuntimeError(
            "could not read /proc/sys/kernel/osrelease (uname -r) from guest"
        )
    return out


def _collections(list_text: str) -> list[str]:
    """The ordered unique collection names of a `kselftest-list.txt`.

    One `collection:test` per line; the collection field can carry `/`
    (net/forwarding), so the split is on the FIRST `:`.
    """
    seen: list[str] = []
    for line in list_text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        name = line.split(":", 1)[0]
        if name and name not in seen:
            seen.append(name)
    return seen


def main(vm_name: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)

    system_state = remote.is_system_running()
    booted = system_state in ("running", "degraded")
    if not booted:
        raise RuntimeError(
            f"{vm_name}: guest not booted (is-system-running={system_state!r}); "
            f"boot it with f/qsu/boot before running the selftests"
        )

    if not remote.unit_exists("kselftest@.service"):
        raise RuntimeError(
            f"{vm_name}: kselftest@.service template missing; compose the guest "
            f"closure with the `selftests` test suite and reboot"
        )

    kernel_version = _kernel_release(remote)
    name = f"kselftests-{kernel_version}"
    store_path = store.local_path(name)
    if not store_path:
        raise RuntimeError(
            f"{vm_name}: no {name} in the store index; build the kernel with "
            f"selftests enabled (the build flow's Selftests group compiles the "
            f"install tree and publishes it under {name})"
        )

    list_file = Path(store_path) / "kselftest-list.txt"
    if not list_file.is_file():
        raise RuntimeError(
            f"{vm_name}: {list_file} missing; {name} is not a kselftest install "
            f"tree (rebuild and republish the kernel's selftests)"
        )
    list_text = list_file.read_text()
    collections = _collections(list_text)
    if not collections:
        raise RuntimeError(
            f"{vm_name}: {list_file} lists no collections: the kernel built no "
            f"selftests; enable them in the build flow's Selftests group"
        )
    tests_total = sum(1 for line in list_text.splitlines() if ":" in line)

    version_file = Path(store_path) / "VERSION"
    version = version_file.read_text().strip() if version_file.is_file() else ""

    cache = collections_cache(vm_name)
    _atomic_write(cache, json.dumps(collections) + "\n")
    print(
        f"+ wrote {cache} ({len(collections)} collections for the run form)",
        flush=True,
    )
    tests = [line.strip() for line in list_text.splitlines() if ":" in line]
    tcache = tests_cache(vm_name)
    _atomic_write(tcache, json.dumps(tests) + "\n")
    print(f"+ wrote {tcache} ({len(tests)} tests for the run form)", flush=True)
    print(
        f"{vm_name}: booted={system_state} kernel={kernel_version} "
        f"store_path={store_path} collections={len(collections)} "
        f"tests={tests_total} version={version or '?'}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "host": system_state,
        "booted": booted,
        "kernel_version": kernel_version,
        "store_path": store_path,
        "collections": collections,
        "tests_total": tests_total,
        "version": version,
    }
