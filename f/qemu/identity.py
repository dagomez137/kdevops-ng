# SPDX-License-Identifier: copyleft-next-0.3.1
"""Compute a build-input identity and key the install prefix on it (runnable step).

QEMU has no kernelrelease/LOCALVERSION to bake the identity into, so the build
identity keys the install prefix instead: a 12-hex hash over the inputs that fix a
QEMU build's bytes (the target list, the configure flags, the compiler, the
toolchain, which is the `build-qemu` devShell's derivation path, and the source
commit) names the per-identity install root `destdir/<identity>`. The configure and install
steps then use that prefix as `--prefix`, so identical inputs install under one
prefix and a built identity can be fetched or reused instead of rebuilt.

The digest hashes only those input strings (no host path), so the identity is the
same on every host. `configure_args` is hashed verbatim; the host-specific
`--prefix`/`-ffile-prefix-map` configure adds itself are not part of the inputs
here, so they never leak into the identity.

Equivalent bash:

    toolchain=$(nix eval --raw "path:$flake#devShells.$system.build-qemu.drvPath")
    commit=$(git -C "$worktree" rev-parse HEAD)
    identity=$(printf '%s\\0%s\\0%s\\0%s\\0%s' \\
        "$target_list" "$configure_args" "$compiler" "$toolchain" "$commit" \\
        | sha256sum | cut -c1-12)
    prefix="$destdir/$identity"
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from f.common.devshell import Git, Nix, vendor_dir


def main(
    worktree: str,
    destdir: str,
    target_list: list[str] | None = None,
    configure_args: str = "",
    compiler: str = "gcc",
) -> dict:
    targets = ",".join(target_list or [])
    commit = Git().capture("-C", worktree, "rev-parse", "HEAD").strip()
    blob = "\0".join([targets, configure_args, compiler, _toolchain(), commit]).encode()
    identity = hashlib.sha256(blob).hexdigest()[:12]
    prefix = str(Path(destdir) / identity)
    print(f"build identity {identity} -> prefix {prefix}", flush=True)
    return {"identity": identity, "prefix": prefix, "destdir": destdir}


def _toolchain() -> str:
    """The build-qemu devShell's derivation path: the toolchain store hash."""
    flake = vendor_dir() / "nixos-flake"
    system = f"{os.uname().machine}-linux"
    return (
        Nix()
        .capture("eval", "--raw", f"path:{flake}#devShells.{system}.build-qemu.drvPath")
        .strip()
    )
