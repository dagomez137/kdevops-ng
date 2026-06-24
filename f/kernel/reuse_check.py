# SPDX-License-Identifier: copyleft-next-0.3.1
"""Report whether a kernel build identity is already installed in the destdir.

Runnable step. The configure step bakes a build identity into kernelrelease, and the
install steps name artifacts by it (`boot/<image>-<release>`, `lib/modules/<release>`).
Run before the expensive compile: if the image and modules for that release are already
in the destdir (or a peer's build for this release is in the Nix store, where
`fetch_identity` leaves it), the build flow skips compile/install/modules and the
manifest points at them, the build is reused not repeated. Wipe the destdir (or set
`reuse=false`) to force a rebuild.

Returns `present` plus the resolved `bzImage`/`boot`/`modules` (under the destdir for a
local install, else under the store path) so the manifest can fall back to them when the
build steps are skipped. `destdir` is always the original install root.
"""

from __future__ import annotations

from pathlib import Path

from f.common import store


def _run_layer(root: str, uts_release: str) -> tuple[str | None, bool]:
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


def main(destdir: str, uts_release: str) -> dict:
    root = destdir
    image, has_modules = _run_layer(destdir, uts_release)
    if not (image and has_modules):
        sp = store.local_path(f"kernel-{uts_release}")
        if sp:
            sp_image, sp_modules = _run_layer(sp, uts_release)
            if sp_image and sp_modules:
                root, image, has_modules = sp, sp_image, sp_modules
    present = bool(image and has_modules)
    boot = Path(root) / "boot"
    print(
        f"identity {uts_release}: present={present} image={image} "
        f"modules={Path(root) / 'lib/modules' / uts_release if has_modules else None}",
        flush=True,
    )
    return {
        "present": present,
        "uts_release": uts_release,
        "bzImage": image,
        "boot": str(boot) if boot.is_dir() else None,
        "modules": str(Path(root) / "lib/modules") if has_modules else None,
        "destdir": destdir,
    }
