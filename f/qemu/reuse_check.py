# SPDX-License-Identifier: copyleft-next-0.3.1
"""Report whether a QEMU build identity is already installed under its prefix.

Runnable step, the QEMU analog of `f/kernel/reuse_check`. The identity step keys the
install prefix `destdir/<identity>`; the install step populates `<prefix>/bin` with the
`qemu-system-*` emulators. Run before the expensive compile: if that prefix already
holds an installed QEMU, the build flow skips configure/compile/install and the
manifest points at it — the build is reused, not repeated. Wipe the prefix (or set
`reuse=false`) to force a rebuild.

Returns `present` plus the resolved `prefix`/`qemu_binary` so the manifest can fall
back to them when the build steps are skipped. Filesystem only — no devShell, robust
if the prefix does not exist.
"""

from __future__ import annotations

from pathlib import Path


def main(prefix: str) -> dict:
    bindir = Path(prefix) / "bin"
    binaries = sorted(bindir.glob("qemu-system-*")) if bindir.is_dir() else []
    qemu_binary = str(binaries[0]) if binaries else None
    present = bool(binaries)
    print(f"identity {prefix}: present={present} qemu_binary={qemu_binary}", flush=True)
    return {"present": present, "prefix": prefix, "qemu_binary": qemu_binary}
