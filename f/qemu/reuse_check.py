# SPDX-License-Identifier: copyleft-next-0.3.1
"""Report whether a QEMU build identity is already installed under its prefix.

Runnable step, the QEMU analog of `f/kernel/reuse_check`. The identity step keys the
install prefix `destdir/<identity>`; the install step populates `<prefix>/bin` with the
`qemu-system-*` emulators. Run before the expensive compile: if that prefix already
holds an installed QEMU (or a peer's build for this identity is in the Nix store, where
`fetch_identity` leaves it) the build flow skips configure/compile/install and the
manifest points at it, the build is reused not repeated. Wipe the prefix (or set
`reuse=false`) to force a rebuild.

The devel layer is reported separately as `devel_present`. The two layers are
independent store paths, so a present run layer says nothing about whether
`qemu-devel-<identity>` was ever published: an identity built before the devel layer
existed, or one whose run layer arrived from a peer, has one and not the other. The
build flow reads the two apart, which is what lets a developer-worktree run rebuild for
the devel layer alone instead of skipping on the run layer and finding nothing to fetch.

Returns `present` plus the resolved `prefix`/`qemu_binary` (the binary under the prefix
for a local install, else under the store path) so the manifest can fall back to them
when the build steps are skipped. Filesystem only: no devShell, robust if neither the
prefix nor a store entry exists.
"""

from __future__ import annotations

from pathlib import Path

from f.common import run_layer, store


def main(prefix: str) -> dict:
    identity = Path(prefix).name
    binaries = run_layer.qemu_emulators(prefix)
    if not binaries:
        sp = store.local_path(f"qemu-{identity}")
        binaries = run_layer.qemu_emulators(sp) if sp else []
    qemu_binary = str(binaries[0]) if binaries else None
    present = bool(binaries)
    devel_present = store.local_path(f"qemu-devel-{identity}") is not None
    print(
        f"identity {prefix}: present={present} devel_present={devel_present} "
        f"qemu_binary={qemu_binary}",
        flush=True,
    )
    return {
        "present": present,
        "devel_present": devel_present,
        "prefix": prefix,
        "qemu_binary": qemu_binary,
    }
