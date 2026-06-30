# SPDX-License-Identifier: copyleft-next-0.3.1
"""Resolve a bringup's reuse artifacts: store-index kernel/QEMU + sidecar closure.

Replaces the legacy per-VM `discover`. A bringup now picks a kernel or QEMU directly
from this host's Nix-store index (`kernel-<release>`, `qemu-<identity>`, written by the
build flows' `publish` step and by `fetch_identity` for a peer's build); this maps the
selected index name back to the boot manifest the qsu render consumes (image + modules
for the kernel, the `qemu-system-*` binary for QEMU). The NixOS closure is not indexed
(Nix content-addresses it, so an unchanged build is a near-instant rebuild); a closure
`reuse` instead replays the `init`/`initrd` and the virtiofs-share contract a refreshed
VM recorded in its render sidecar. Always probes the host operator (the closure build's
`home_dir` default).

A `reuse` pick that no longer resolves raises, so the render never boots a kernelless or
closureless VM (it refuses one anyway). A QEMU reuse with no pick falls back to the most
recently indexed QEMU; a kernel reuse with no pick raises early, at this step.

Equivalent command:

    readlink "$SYSTEM_DIR/store-index/<kernel_index>"
    cat "$WORKERS_DIR/shared/vm/<vm_name>.vars.json"   # closure reuse
"""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path

from f.common import run_layer, store


def host_operator() -> str:
    """The host operator's username, for the guest's `/home/<user>` (root's home)."""
    home = os.environ.get("HOME", "").rstrip("/")
    if home.startswith("/home/"):  # rstrip first, so a bare "/home/" falls through
        name = os.path.basename(home)
    else:
        try:
            name = getpass.getuser()
        except Exception:
            name = ""
    return name if name and name not in (".", "..", "root") else "kdevops"


def _kernel(name: str) -> dict:
    if not name:
        return {}
    sp = store.local_path(name)
    if not sp:
        raise ValueError(f"no store-index entry {name!r} (GC'd or never published)")
    release = name.removeprefix("kernel-")
    image, has_modules = run_layer.kernel_run_layer(sp, release)
    if not (image and has_modules):
        raise ValueError(f"{name} at {sp} has no image/modules for {release}")
    return {
        "bzImage": image,
        "modules": str(Path(sp) / "lib/modules"),
        "uts_release": release,
    }


def _qemu(name: str) -> str | None:
    if not name:
        return None
    sp = store.local_path(name)
    binaries = run_layer.qemu_emulators(sp) if sp else []
    if not binaries:
        raise ValueError(f"no store-index entry {name!r} with a qemu-system binary")
    return str(binaries[0])


def _closure(reuse: bool, vm_name: str) -> tuple[dict, dict]:
    """The reused closure (`init`/`initrd`) + its share contract, from a VM's sidecar."""
    if not reuse:
        return {}, {}
    sidecar = Path(os.environ["WORKERS_DIR"]) / "shared/vm" / f"{vm_name}.vars.json"
    try:
        data = json.loads(sidecar.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(
            f"closure reuse needs {vm_name!r}'s render sidecar {sidecar}: {e}"
        ) from None
    closure = data.get("closure") or {}
    if not closure.get("init"):
        raise ValueError(f"{sidecar} records no closure init/initrd to reuse")
    return {"init": closure.get("init"), "initrd": closure.get("initrd")}, (
        data.get("sharing") or {}
    )


def main(
    kernel_index: str = "",
    kernel_reuse: bool = False,
    qemu_index: str = "",
    qemu_reuse: bool = False,
    closure_reuse: bool = False,
    vm_name: str = "",
) -> dict:
    if qemu_reuse and not qemu_index:
        qemu_index = store.latest_index("qemu-")
        if not qemu_index:
            raise ValueError(
                "QEMU mode is reuse but no QEMU is in the store index: build one "
                "with f/qemu/build, or set the QEMU mode to nixpkgs."
            )
        print(f"resolve: qemu reuse auto-picked latest {qemu_index}", flush=True)
    if kernel_reuse and not kernel_index:
        raise ValueError(
            "Kernel mode is reuse but no kernel was picked: choose one under "
            "Reuse kernel, or set the Kernel mode to build."
        )
    closure, sharing = _closure(closure_reuse, vm_name)
    out = {
        "kernel": _kernel(kernel_index),
        "qemu_binary": _qemu(qemu_index),
        "closure": closure,
        "sharing": sharing,
        "host_user": host_operator(),
    }
    print(
        f"resolve: kernel={'set' if out['kernel'] else 'none'} "
        f"qemu_binary={out['qemu_binary'] or 'none'} "
        f"closure={'set' if out['closure'] else 'none'} host_user={out['host_user']}",
        flush=True,
    )
    return out
