# SPDX-License-Identifier: copyleft-next-0.3.1
"""Install the built kernel image into a separate destdir (no modules).

`make install` is the canonical install target, but the kernel's scripts/install.sh
delegates the actual copy to an install hook it searches for in this order:

    $HOME/bin/installkernel  ->  /sbin/installkernel  ->  arch/$arch/boot/install.sh

On a distro container /sbin/installkernel exists (debianutils) and the arch fallback
probes for lilo -- neither is reproducible. So, exactly as nixpkgs' kernel build
does, we provide the highest-priority hook ($HOME/bin/installkernel) as a tiny script
that just copies what make install hands it. make install still resolves the
arch-correct image path (KBUILD_IMAGE); our hook only copies, so the result never
depends on the container's distro tooling.

    destdir/boot/   INSTALL_PATH — the kernel image + System.map (unversioned, like nixpkgs)

Modules are a separate, independently-skippable step (f/kernel/install_modules); an
all-built-in kernel needs only this one.

Equivalent bash, run inside the nixos-flake build devShell:

    mkdir --parents "$HOME/bin"
    printf '#!/bin/sh\\nset -e\\ncp --archive --verbose "$2" "$4"\\ncp --archive --verbose "$3" "$4"\\n' > "$HOME/bin/installkernel"
    chmod +x "$HOME/bin/installkernel"
    make --directory="$worktree" O="$build_dir" $make_flags INSTALL_PATH="$destdir/boot" install
"""

from __future__ import annotations

import os
import shlex
import tempfile
from pathlib import Path

from f.common.devshell import DevShell

# The kernel passes the hook: $1 release, $2 image, $3 System.map, $4 INSTALL_PATH.
# Adapted from nixpkgs pkgs/os-specific/linux/kernel/build.nix (long-form flags).
_INSTALLKERNEL = '#!/bin/sh\nset -e\ncp --archive --verbose "$2" "$4"\ncp --archive --verbose "$3" "$4"\n'


def main(
    worktree: str,
    build_dir: str,
    destdir: str = "",
    make_flags: str = "",
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    build = Path(build_dir)

    # Install destination is separate from the build dir; default to the slot-level
    # destdir alongside the source worktree.
    dest = Path(destdir) if destdir else Path(worktree).parent / "destdir"
    boot = dest / "boot"
    boot.mkdir(parents=True, exist_ok=True)

    # Stage our installkernel hook and point HOME at it so make install finds it
    # before /sbin/installkernel (reproducible, no distro dependency).
    home = Path(tempfile.mkdtemp(prefix="kbuild-home-"))
    hook = home / "bin" / "installkernel"
    hook.parent.mkdir(parents=True)
    hook.write_text(_INSTALLKERNEL)
    hook.chmod(0o755)

    flag_args = shlex.split(make_flags)
    shell = DevShell(workers)
    shell.run("make", f"--directory={worktree}", f"O={build}", *flag_args,
              f"INSTALL_PATH={boot}", "install", env={"HOME": str(home)})

    print(f"installed kernel image -> {boot}", flush=True)
    return {"destdir": str(dest), "boot": str(boot)}
