# SPDX-License-Identifier: copyleft-next-0.3.1
"""Compute a build-input identity and key the install prefix on it (runnable step).

QEMU has no kernelrelease/LOCALVERSION to bake the identity into, so the build
identity keys the install prefix instead: a 12-hex hash over the inputs that fix a
QEMU build's bytes (the target list, the configure flags, the compiler, the
toolchain, which is the `build-qemu` devShell's derivation path, and the source
tree) names the per-identity install root. The QEMU version (from
`<worktree>/VERSION`, e.g. `11.0.0`, the analog of the kernel's `make
kernelversion`) leads it and a readable label follows when present, giving
`destdir/<version>-<label>-<identity>` (else `destdir/<version>-<identity>`), and
the matching `qemu-<version>-<label>-<identity>` store key `f/qemu/publish` derives
from the prefix name, so a stock v11.0.0 tag build reads `qemu-11.0.0-vanilla-<identity>`
instead of a bare hash. The configure and install steps then use that prefix as
`--prefix`, so identical inputs install under one prefix and a built identity can be
fetched or reused instead of rebuilt.

The digest hashes only those input strings (no host path), so the identity is the
same on every host. `configure_args` is hashed verbatim; the host-specific
`--prefix`/`-ffile-prefix-map` configure adds itself are not part of the inputs
here, so they never leak into the identity. The label comes from
`f.common.worktree.prepare` (a custom override, the b4 series subject, `vanilla`
for an upstream tag, or a slug of the dev ref) and is cosmetic: only the prefix and
store name carry it, while the returned `identity` stays the bare 12-hex content
hash, so two configs of one ref share the label and differ only in the identity.

Equivalent bash:

    toolchain=$(nix eval --raw "path:$flake#devShells.$system.build-qemu.drvPath")
    tree=$(git -C "$worktree" rev-parse "HEAD^{tree}")
    identity=$(printf '%s\\0%s\\0%s\\0%s\\0%s' \\
        "$target_list" "$configure_args" "$compiler" "$toolchain" "$tree" \\
        | sha256sum | cut -c1-12)
    version=$(cat "$worktree/VERSION")            # e.g. 11.0.0
    prefix="$destdir/$version-${label:+$label-}$identity"
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from f.common.devshell import Git, Nix, vendor_dir
from f.common.worktree import _slug, _split_trailing_version


def main(
    worktree: str,
    destdir: str,
    target_list: list[str] | None = None,
    configure_args: str = "",
    compiler: str = "gcc",
    label: str = "",
) -> dict:
    targets = ",".join(target_list or [])
    tree = Git().capture("-C", worktree, "rev-parse", "HEAD^{tree}").strip()
    blob = "\0".join([targets, configure_args, compiler, _toolchain(), tree]).encode()
    identity = hashlib.sha256(blob).hexdigest()[:12]
    version = _read_version(worktree)
    prefix = str(Path(destdir) / _prefix_basename(version, label, identity))
    print(f"build identity {identity} -> prefix {prefix}", flush=True)
    return {"identity": identity, "prefix": prefix, "destdir": destdir}


def _read_version(worktree: str) -> str:
    """The QEMU version from `<worktree>/VERSION` (e.g. `11.0.0`, no `v`), the analog
    of the kernel's `make kernelversion`; empty when the file is absent."""
    path = Path(worktree) / "VERSION"
    return path.read_text().strip() if path.is_file() else ""


def _prefix_basename(version: str, label: str, identity: str) -> str:
    """Lead the install prefix with the QEMU version, mirroring the kernel's
    version-first release: `<version>-<label>-<identity>` with a label,
    `<version>-<identity>` without. The label slug takes a flat 64-char sanity cap
    (no uname budget here), but a matched `-v<N>` revision suffix is preserved
    through that cap. A missing VERSION falls back to the label-only form."""
    head, suffix = _split_trailing_version(_slug(label))
    slug = head[: 64 - len(suffix)].rstrip("-._") + suffix
    parts = [p for p in (version, slug) if p]
    parts.append(identity)
    return "-".join(parts)


def _toolchain() -> str:
    """The build-qemu devShell's derivation path: the toolchain store hash."""
    flake = vendor_dir() / "nixos-flake"
    system = f"{os.uname().machine}-linux"
    return (
        Nix()
        .capture("eval", "--raw", f"path:{flake}#devShells.{system}.build-qemu.drvPath")
        .strip()
    )
