# SPDX-License-Identifier: copyleft-next-0.3.1
"""Report whether a QEMU build identity is already installed under its prefix.

Runnable step, the QEMU analog of `f/kernel/reuse_check`. The identity step keys the
install prefix `destdir/<identity>`; the install step populates `<prefix>/bin` with the
`qemu-system-*` emulators. Run before the expensive compile: if that prefix already
holds an installed QEMU (or a peer's build for this identity is in the Nix store, where
`fetch_identity` leaves it) the build flow skips configure/compile/install and the
manifest points at it, the build is reused not repeated. Wipe the prefix (or set
`reuse=false`) to force a rebuild.

Returns `present` plus the resolved `prefix`/`qemu_binary` (the binary under the prefix
for a local install, else under the store path) so the manifest can fall back to them
when the build steps are skipped. Filesystem only: no devShell, robust if neither the
prefix nor a store entry exists.
"""

from __future__ import annotations

from pathlib import Path

from f.common import store


def _emulators(root: str) -> list[Path]:
    bindir = Path(root) / "bin"
    return sorted(bindir.glob("qemu-system-*")) if bindir.is_dir() else []


def main(prefix: str) -> dict:
    binaries = _emulators(prefix)
    if not binaries:
        sp = store.local_path(f"qemu-{Path(prefix).name}")
        binaries = _emulators(sp) if sp else []
    qemu_binary = str(binaries[0]) if binaries else None
    present = bool(binaries)
    print(f"identity {prefix}: present={present} qemu_binary={qemu_binary}", flush=True)
    return {"present": present, "prefix": prefix, "qemu_binary": qemu_binary}
