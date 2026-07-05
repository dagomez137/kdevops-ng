# SPDX-License-Identifier: copyleft-next-0.3.1
"""Discover a booted guest's usertests readiness over vsock-SSH (read-only).

Checks the guest is up and usertests-ready (the `usertests@.service` template
installed), reads the running kernel release, and resolves the matching built
harness tree from the local Nix store (the `usertests-<kver>` index entry
`f/kernel/build_usertests` publishes). The tree's `MANIFEST` (one
`<dir>/<binary>` line per harness) enumerates the run items, in list order. A
kernel with no published tree, or a MANIFEST listing zero harnesses, fails
here: a run would silently test nothing. Writes the item list to the per-VM
picker cache on the share. Mutates nothing on the guest.

Keying by the booted kernel release couples the artifact to the build, but the
harnesses are USERSPACE binaries compiled from that build's SOURCE tree
(tools/testing/{radix-tree,vma,rbtree,memblock,scatterlist}): a run exercises
the source's data-structure code in userspace, and the guest only hosts the
run; the booted kernel itself is not what is under test.

Equivalent commands:

    systemctl --host <vm> is-system-running
    systemctl --host <vm> list-unit-files usertests@.service
    ssh <vm> cat /proc/sys/kernel/osrelease
    readlink "$SYSTEM_DIR/store-index/usertests-<kver>"
    cat <store>/MANIFEST
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from f.common import store
from f.common.remote import RemoteSystemd
from f.common.remote import list_vms as _list_vms
from f.usertests.common import _atomic_write, harnesses_cache


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


def _harnesses(manifest_text: str) -> list[str]:
    """The ordered unique `<dir>/<binary>` items of a MANIFEST."""
    seen: list[str] = []
    for line in manifest_text.splitlines():
        line = line.strip()
        if not line or "/" not in line:
            continue
        if line not in seen:
            seen.append(line)
    return seen


def main(vm_name: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)

    system_state = remote.is_system_running()
    booted = system_state in ("running", "degraded")
    if not booted:
        raise RuntimeError(
            f"{vm_name}: guest not booted (is-system-running={system_state!r}); "
            f"boot it with f/qsu/boot before running the usertests"
        )

    if not remote.unit_exists("usertests@.service"):
        raise RuntimeError(
            f"{vm_name}: usertests@.service template missing; compose the guest "
            f"closure with the `usertests` test suite and reboot"
        )

    kernel_version = _kernel_release(remote)
    name = f"usertests-{kernel_version}"
    store_path = store.local_path(name)
    if not store_path:
        raise RuntimeError(
            f"{vm_name}: no {name} in the store index; build the kernel with "
            f"the Usertests group (the build flow compiles the tools/testing "
            f"harness binaries and publishes them under {name})"
        )

    manifest_file = Path(store_path) / "MANIFEST"
    if not manifest_file.is_file():
        raise RuntimeError(
            f"{vm_name}: {manifest_file} missing; {name} is not a usertests "
            f"tree (rebuild and republish the kernel's usertests)"
        )
    harnesses = _harnesses(manifest_file.read_text())
    if not harnesses:
        raise RuntimeError(
            f"{vm_name}: {manifest_file} lists no harnesses: the kernel built "
            f"no usertests; enable them in the build flow's Usertests group"
        )
    missing = [h for h in harnesses if not (Path(store_path) / h).is_file()]
    if missing:
        raise RuntimeError(
            f"{vm_name}: {name} MANIFEST lists binaries absent from the tree: "
            f"{', '.join(missing)}"
        )

    cache = harnesses_cache(vm_name)
    _atomic_write(cache, json.dumps(harnesses) + "\n")
    print(
        f"+ wrote {cache} ({len(harnesses)} harnesses for the run form)",
        flush=True,
    )
    print(
        f"{vm_name}: booted={system_state} kernel={kernel_version} "
        f"store_path={store_path} harnesses={len(harnesses)}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "host": system_state,
        "booted": booted,
        "kernel_version": kernel_version,
        "store_path": store_path,
        "harnesses": harnesses,
    }
