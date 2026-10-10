# SPDX-License-Identifier: copyleft-next-0.3.1
"""Build the NVIDIA open GPU kernel modules against this kernel and install them.

Runnable step, after `install_modules` and before `publish`, so the run layer a
guest boots carries nvidia, nvidia-uvm, nvidia-modeset, nvidia-drm and
nvidia-peermem next to the kernel's own modules, and loads them from the same
/lib/modules share. The source is the release the closure's `gpu` profile ships
the userspace of (see f/kernel/nvidia), copied out of the store because the
build writes into its tree. The copy is `nvidia-open` in the worker's sandbox,
beside the kernel worktree rather than in its build directory, so
`publish_devel` never ships it; it is removed before the build and after
`modules_install`. The modules build against the kernel's source and build trees
with the kernel's own GCC, and `modules_install` places them under
`lib/modules/<release>/kernel/drivers/video` and reruns depmod.

The flow skips it on reuse: reuse_check counts an identity as present only when
it already holds this release, and refuses a kernel that cannot carry the
modules before the compile. The kernel must build with GCC (a Clang or `LLVM=`
kernel is refused), and its .config must set CONFIG_MODULES and
CONFIG_DMA_SHARED_BUFFER; the `imageless_defconfig` preset does, through
CONFIG_SYNC_FILE, and the `virt/gpu-passthrough` fragment does, through
CONFIG_DRM.

Equivalent bash:

    nix=(nix --extra-experimental-features 'nix-command flakes')
    dev=("${nix[@]}" develop "path:$VENDOR_DIR/nixos-flake#build-kernel" --command)
    src=$("${nix[@]}" build --no-link --print-out-paths \\
        "path:$VENDOR_DIR/nixos-flake#nvidia-open-src")
    tree="$WORKERS_DIR/$WORKER_INDEX/main/nvidia-open"
    "${dev[@]}" rm --recursive --force "$tree"
    "${dev[@]}" cp --recursive "$src" "$tree"
    "${dev[@]}" chmod --recursive u+w "$tree"
    "${dev[@]}" make --directory="$tree" --jobs="$(nproc)" modules \\
        SYSSRC="$worktree" SYSOUT="$build_dir" $toolchain
    "${dev[@]}" make --directory="$tree" modules_install \\
        SYSSRC="$worktree" SYSOUT="$build_dir" $toolchain INSTALL_MOD_PATH="$destdir"
    "${dev[@]}" rm --recursive --force "$tree"
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from f.common.devshell import DevShell
from f.kernel import nvidia


def main(
    worktree: str,
    build_dir: str,
    destdir: str,
    uts_release: str,
    make_flags: str = "",
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    nvidia.refuse_unsupported(build_dir, make_flags)
    toolchain = nvidia.toolchain_flags(make_flags)
    src, version = nvidia.source(workers)

    shell = DevShell(workers)
    # Trees an earlier layout left inside the build dir, where publish_devel ships them.
    for stale in sorted(Path(build_dir).glob("nvidia-open-*")):
        shell.run("rm", "--recursive", "--force", str(stale))
    tree = workers / os.environ["WORKER_INDEX"] / "main" / "nvidia-open"
    shell.run("rm", "--recursive", "--force", str(tree))
    shell.run("cp", "--recursive", src, str(tree))
    shell.run("chmod", "--recursive", "u+w", str(tree))

    kernel = [f"SYSSRC={worktree}", f"SYSOUT={build_dir}"]
    try:
        shell.run(
            "make",
            f"--directory={tree}",
            f"--jobs={len(os.sched_getaffinity(0))}",
            "modules",
            *kernel,
            *toolchain,
        )
    except subprocess.CalledProcessError:
        print(
            f"NVIDIA {version} failed to build against {uts_release}; the pinned "
            "release is in vendor/nixos-flake/pkgs/nvidia-driver.nix",
            flush=True,
        )
        raise
    shell.run(
        "make",
        f"--directory={tree}",
        "modules_install",
        *kernel,
        *toolchain,
        f"INSTALL_MOD_PATH={destdir}",
    )
    shell.run("rm", "--recursive", "--force", str(tree))

    installed = nvidia.installed_version(destdir, uts_release, workers)
    if installed != version:
        raise RuntimeError(
            f"modules_install left nvidia.ko at {installed!r}, expected {version}"
        )
    modules = sorted(
        str(p) for p in Path(destdir, "lib/modules", uts_release).rglob("nvidia*.ko*")
    )
    print(f"installed NVIDIA {version} -> {', '.join(modules)}", flush=True)
    return {"version": version, "modules": modules}
