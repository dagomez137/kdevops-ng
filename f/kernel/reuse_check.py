# SPDX-License-Identifier: copyleft-next-0.3.1
"""Report whether a kernel build identity is already installed in the destdir.

Runnable step. The configure step bakes a build identity into kernelrelease, and the
install steps name artifacts by it (`boot/<image>-<release>`, `lib/modules/<release>`).
Run before the expensive compile: if the image and modules for that release are already
in the destdir, the build flow skips compile/install/modules and the manifest points at
them — the build is reused, not repeated. Wipe the destdir (or set `reuse=false`) to
force a rebuild.

Returns `present` plus the resolved `bzImage`/`modules`/`destdir` so the manifest can
fall back to them when the build steps are skipped.
"""

from __future__ import annotations

from pathlib import Path


def main(destdir: str, uts_release: str) -> dict:
    boot = Path(destdir) / "boot"
    modules = Path(destdir) / "lib/modules" / uts_release
    images = ([p for p in sorted(boot.glob(f"*-{uts_release}"))
               if not p.name.startswith(("System.map", "config"))]
              if boot.is_dir() else [])
    bzImage = str(images[0]) if images else None
    present = bool(images) and modules.is_dir()
    print(f"identity {uts_release}: present={present} image={bzImage} "
          f"modules={modules if modules.is_dir() else None}", flush=True)
    return {
        "present": present,
        "uts_release": uts_release,
        "bzImage": bzImage,
        "boot": str(boot) if boot.is_dir() else None,
        "modules": str(Path(destdir) / "lib/modules") if modules.is_dir() else None,
        "destdir": destdir,
    }
