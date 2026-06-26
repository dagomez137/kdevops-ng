# SPDX-License-Identifier: copyleft-next-0.3.1
"""Resolve a build's run layer from an install root (library, not a runnable step).

Imported with:  from f.common import run_layer

A run layer is the bootable subset a build installs and `publish` adds to the Nix store:
for the kernel the `boot/<image>-<release>` image plus `lib/modules/<release>/`; for QEMU
the `bin/qemu-system-*` emulators. The kernel/qemu `reuse_check` steps and the qsu
`resolve` step share this resolution so a release/identity maps to the same paths whether
it sits in a worker destdir or under a `/nix/store` path.
"""

from __future__ import annotations

from pathlib import Path


def main():
    """Library module imported by the build/resolve steps, not a runnable step."""
    return "f/common/run_layer: install-root run-layer resolution"


def kernel_run_layer(root: str, uts_release: str) -> tuple[str | None, bool]:
    """The boot image and modules presence for `uts_release` under `root`.

    Returns `(image, has_modules)`: the boot image named `*-<release>` (excluding its
    `System.map`/`config` siblings) and whether `lib/modules/<release>/` exists.
    """
    boot = Path(root) / "boot"
    images = (
        [
            p
            for p in sorted(boot.glob(f"*-{uts_release}"))
            if not p.name.startswith(("System.map", "config"))
        ]
        if boot.is_dir()
        else []
    )
    image = str(images[0]) if images else None
    has_modules = (Path(root) / "lib/modules" / uts_release).is_dir()
    return image, has_modules


def qemu_emulators(root: str) -> list[Path]:
    """The installed `qemu-system-*` emulators under `<root>/bin`, sorted."""
    bindir = Path(root) / "bin"
    return sorted(bindir.glob("qemu-system-*")) if bindir.is_dir() else []
