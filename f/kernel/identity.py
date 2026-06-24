# SPDX-License-Identifier: copyleft-next-0.3.1
"""Bake a build-input identity into kernelrelease via CONFIG_LOCALVERSION (library).

Imported with:  from f.kernel.identity import bake_identity

A build's identity is a short hash over the inputs that fix its bytes: the `.config`,
the toolchain (the `build-kernel` devShell's derivation path), the make flags, and the
source commit, appended to any existing CONFIG_LOCALVERSION so `make kernelrelease`
(and the booted `uname -r`) self-report it: `7.1.0-rc7-<hash>`, or
`7.1.0-rc7-series-<hash>` when the config already carries a localversion. Same identity
then means same bytes, so the image and modules install under one release and a built
identity can be fetched or reused instead of rebuilt.

The hash excludes the CONFIG_LOCALVERSION line and the host-specific
`-fdebug-prefix-map` value from the make flags, so the identity is the same on every
host.

Equivalent bash, run inside the nixos-flake build devShell:

    # CONFIG_LOCALVERSION=<existing>-<hash>, regenerate auto.conf, read the release back
    make --directory="$worktree" O="$build" $make_flags syncconfig
    make --silent --directory="$worktree" O="$build" $make_flags kernelrelease
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
from pathlib import Path

from f.common.devshell import Git, Nix, vendor_dir


def main():
    """This module is a library imported by the configure steps, not a runnable step."""
    return "f/kernel/identity: build-identity helper"


def bake_identity(shell, worktree: str, build_dir: str, make_flags: str = "") -> str:
    config = Path(build_dir) / ".config"
    text = config.read_text()
    digest = _digest(text, worktree, make_flags)
    # strip a prior identity before re-appending.
    base = re.sub(r"-[0-9a-f]{12}$", "", _localversion(text))
    _set_localversion(config, f"{base}-{digest}")

    base = ["make", f"--directory={worktree}", f"O={build_dir}"]
    flags = shlex.split(make_flags)
    # syncconfig regenerates auto.conf.
    shell.run(*base, *flags, "syncconfig")
    release = shell.capture(*base, "--silent", *flags, "kernelrelease").strip()
    print(f"build identity {digest} -> {release}", flush=True)
    return release


def _digest(config_text: str, worktree: str, make_flags: str) -> str:
    """A 12-hex hash over the inputs that fix a build's bytes (host-independent)."""
    config = "\n".join(line for line in config_text.splitlines()
                       if not line.startswith("CONFIG_LOCALVERSION="))
    flags = re.sub(r"-fdebug-prefix-map=\S*", "-fdebug-prefix-map=", make_flags)
    commit = Git().capture("-C", worktree, "rev-parse", "HEAD").strip()
    blob = "\0".join([config, _toolchain(), flags, commit]).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def _toolchain() -> str:
    """The build-kernel devShell's derivation path: the toolchain store hash."""
    flake = vendor_dir() / "nixos-flake"
    system = f"{os.uname().machine}-linux"
    return Nix().capture(
        "eval", "--raw", f"path:{flake}#devShells.{system}.build-kernel.drvPath").strip()


def _localversion(config_text: str) -> str:
    """The existing CONFIG_LOCALVERSION string (e.g. a series prefix), else empty."""
    for line in config_text.splitlines():
        if line.startswith("CONFIG_LOCALVERSION="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _set_localversion(config: Path, value: str) -> None:
    """Rewrite CONFIG_LOCALVERSION in the .config; syncconfig reconciles the rest."""
    lines = []
    found = False
    for line in config.read_text().splitlines():
        if line.startswith("CONFIG_LOCALVERSION="):
            lines.append(f'CONFIG_LOCALVERSION="{value}"')
            found = True
        else:
            lines.append(line)
    if not found:
        lines.append(f'CONFIG_LOCALVERSION="{value}"')
    config.write_text("\n".join(lines) + "\n")
    print(f"wrote {config}  CONFIG_LOCALVERSION={value!r}", flush=True)
