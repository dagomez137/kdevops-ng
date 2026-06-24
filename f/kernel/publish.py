# SPDX-License-Identifier: copyleft-next-0.3.1
"""Publish an installed kernel identity's run layer to the Nix store.

Runnable step, the publish half of the Store transport (the `reuse_check`/`fetch_identity`
family). Run only after a real install (the flow skips it on reuse): stage just this
release's run layer, the boot image artifacts (`boot/<image>-<release>`,
`System.map-<release>`, `config-<release>`) and the `lib/modules/<release>/` tree, not
the whole multi-release destdir, and add it to the store. A peer can then fetch it by
release with `nix copy`. The store path is identical on every host.

Returns the index `name`, the resolved `store_path`, the `uts_release`, and the run
layer resolved inside the store (`bzImage`, `boot`, `modules`) so the manifest can
point a cross-worker-group boot at `/nix/store` instead of this worker's local destdir.

Equivalent bash, the staged tree then added to the store:

    cp --recursive --force "$destdir"/boot/*-"$uts_release" "$stage"/boot/
    cp --recursive --force "$destdir"/lib/modules/"$uts_release" \\
        "$stage"/lib/modules/"$uts_release"
    rm --force "$stage"/lib/modules/"$uts_release"/build \\
        "$stage"/lib/modules/"$uts_release"/source   # dangling worktree symlinks
    nix store add-path "$stage" --name kernel-"$uts_release"
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from f.common import store
from f.common.devshell import run_logged


def main(destdir: str, uts_release: str) -> dict:
    name = f"kernel-{uts_release}"
    src = Path(destdir)
    images = sorted(src.glob(f"boot/*-{uts_release}"))

    stage = Path(tempfile.mkdtemp(prefix=f"{name}-"))
    try:
        (stage / "boot").mkdir(parents=True, exist_ok=True)
        (stage / "lib/modules").mkdir(parents=True, exist_ok=True)
        for image in images:
            run_logged(["cp", "--recursive", "--force",
                        str(image), str(stage / "boot" / image.name)])
        run_logged(["cp", "--recursive", "--force",
                    str(src / "lib/modules" / uts_release),
                    str(stage / "lib/modules" / uts_release)])
        # The kbuild build/source symlinks are absolute paths into the builder's
        # worktree; they dangle on a peer fetch and under the read-only store mount.
        mod_stage = stage / "lib/modules" / uts_release
        for link in ("build", "source"):
            target = mod_stage / link
            if target.is_symlink():
                target.unlink()
                print(f"stripped {uts_release}/{link} (dangling worktree symlink)", flush=True)
        sp = store.publish(name, str(stage))
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    boot_images = [i for i in images if not i.name.startswith(("System.map", "config"))]
    bzImage = str(Path(sp) / "boot" / boot_images[0].name) if boot_images else None
    return {
        "name": name,
        "store_path": sp,
        "uts_release": uts_release,
        "bzImage": bzImage,
        "boot": str(Path(sp) / "boot"),
        "modules": str(Path(sp) / "lib/modules"),
    }
