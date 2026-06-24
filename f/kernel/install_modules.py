# SPDX-License-Identifier: copyleft-next-0.3.1
"""Install kernel modules into a separate destdir (INSTALL_MOD_PATH).

`make modules_install` installs to $(INSTALL_MOD_PATH)/lib/modules/$(KERNELRELEASE)
(Documentation/kbuild/kbuild.rst, modules.rst). INSTALL_MOD_PATH is a prefix used for
build-root relocation, distinct from the `O=` build dir, so modules stage under a
per-slot destdir and never touch the host /lib/modules:

    destdir/lib/modules/<release>/   INSTALL_MOD_PATH

Skip this step for an all-built-in kernel (CONFIG_MODULES=n); there is nothing to
install. The modules were already built by the default `make` (CONFIG_MODULES puts
them in the `all` goal), so no separate `make modules` is needed.

`modules_install` creates the `build` symlink (-> the kbuild output dir) but not the
matching canonical `source` symlink (-> the source tree). Tools that chase kbuild
path-resolution (bpftrace, perf probe, libbpf-tools, out-of-tree module builds --
include/linux/kconfig.h is the common trip-wire) expect both, so `source_symlink`
(default on) adds it pointing at the worktree.

Equivalent bash, run inside the nixos-flake build devShell:

    make --directory="$worktree" O="$build_dir" $make_flags INSTALL_MOD_PATH="$destdir" modules_install
    ln --symbolic --force "$worktree" "$destdir/lib/modules/$release/source"
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from f.common.devshell import DevShell


def main(
    worktree: str,
    build_dir: str,
    destdir: str = "",
    make_flags: str = "",
    source_symlink: bool = True,
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    build = Path(build_dir)

    # Install destination is separate from the build dir; default to the slot-level
    # destdir alongside the source worktree.
    dest = Path(destdir) if destdir else Path(worktree).parent / "destdir"
    dest.mkdir(parents=True, exist_ok=True)

    flag_args = shlex.split(make_flags)
    shell = DevShell(workers)
    shell.run("make", f"--directory={worktree}", f"O={build}", *flag_args,
              f"INSTALL_MOD_PATH={dest}", "modules_install")

    modules = dest / "lib/modules"
    source = None
    if source_symlink:
        # Pair the `build` symlink modules_install created with the canonical
        # `source` symlink (-> the worktree) for the release just installed.
        release = shell.capture("make", "--silent", f"--directory={worktree}",
                                f"O={build}", "kernelrelease").strip()
        link = modules / release / "source"
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(worktree)
        source = str(link)
        print(f"source symlink {link} -> {worktree}", flush=True)

    print(f"installed modules -> {modules}", flush=True)
    return {"destdir": str(dest), "modules": str(modules), "source": source}
