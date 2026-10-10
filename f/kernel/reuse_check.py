# SPDX-License-Identifier: copyleft-next-0.3.1
"""Report whether a kernel build identity is already installed in the destdir.

Runnable step. The configure step bakes a build identity into kernelrelease, and the
install steps name artifacts by it (`boot/<image>-<release>`, `lib/modules/<release>`).
Run before the expensive compile: if the image and modules for that release are already
in the destdir (or a peer's build for this release is in the Nix store, where
`fetch_identity` leaves it), the build flow skips compile/install/modules and the
manifest points at them, the build is reused not repeated. Wipe the destdir (or set
`reuse=false`) to force a rebuild.

The devel layer is reported separately as `devel_present`. The two layers are
independent store paths, so a present run layer says nothing about whether
`kernel-devel-<release>` was ever published: an identity built before the devel layer
existed, or one whose run layer arrived from a peer, has one and not the other. The
build flow reads the two apart, which is what lets a developer-worktree run rebuild for
the devel layer alone instead of skipping on the run layer and finding nothing to fetch.
A devel layer that resolves but carries no `.config` predates the allowlist that ships
the Rust index inputs, so it counts as absent and is republished once instead of
indexing worktrees forever with no Rust half; every kernel build writes a `.config`
whether or not `CONFIG_RUST` is set, so the probe cannot misfire on a kernel built
without Rust.

With `nvidia`, the run layer counts as present only when it holds the NVIDIA open
GPU modules at the release the closure's driver is (see f/kernel/nvidia): an identity
built without them, or with another release, is rebuilt rather than booted with a
driver its modules refuse. Given `build_dir`, an identity about to be rebuilt for them
is first checked against `make_flags` and the configured `.config`, so a kernel that
cannot carry the modules is refused here and not after the compile.

Returns `present` plus the resolved `bzImage`/`boot`/`modules` (under the destdir for a
local install, else under the store path) so the manifest can fall back to them when the
build steps are skipped. `destdir` is always the original install root.
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common import run_layer, store
from f.kernel import nvidia as nvidia_modules


def main(
    destdir: str,
    uts_release: str,
    nvidia: bool = False,
    build_dir: str = "",
    make_flags: str = "",
) -> dict:
    root = destdir
    image, has_modules = run_layer.kernel_run_layer(destdir, uts_release)
    if not (image and has_modules):
        sp = store.local_path(f"kernel-{uts_release}")
        if sp:
            sp_image, sp_modules = run_layer.kernel_run_layer(sp, uts_release)
            if sp_image and sp_modules:
                root, image, has_modules = sp, sp_image, sp_modules
    present = bool(image and has_modules)
    nvidia_version = None
    if nvidia and present:
        workers = Path(os.environ["WORKERS_DIR"])
        _, want = nvidia_modules.source(workers)
        nvidia_version = nvidia_modules.installed_version(root, uts_release, workers)
        if nvidia_version != want:
            print(
                f"identity {uts_release} holds NVIDIA {nvidia_version}, "
                f"needs {want}: rebuilding",
                flush=True,
            )
            present = False
    if nvidia and not present and build_dir:
        nvidia_modules.refuse_unsupported(build_dir, make_flags)
    devel = store.local_path(f"kernel-devel-{uts_release}")
    devel_present = devel is not None and Path(devel, ".config").is_file()
    boot = Path(root) / "boot"
    print(
        f"identity {uts_release}: present={present} devel_present={devel_present} "
        f"image={image} "
        f"modules={Path(root) / 'lib/modules' / uts_release if has_modules else None}",
        flush=True,
    )
    out = {
        "present": present,
        "devel_present": devel_present,
        "uts_release": uts_release,
        "bzImage": image,
        "boot": str(boot) if boot.is_dir() else None,
        "modules": str(Path(root) / "lib/modules") if has_modules else None,
        "destdir": destdir,
    }
    if nvidia:
        out["nvidia_version"] = nvidia_version
    return out
