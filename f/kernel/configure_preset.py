# SPDX-License-Identifier: copyleft-next-0.3.1
"""Configure method `preset`: apply a complete predefined kernel config.

A preset is a whole-kernel config shipped in the linux-config-fragments library
(`defconfigs/`), applied with the kernel's own KCONFIG_ALLCONFIG mechanism
(Documentation/kbuild/kconfig.rst): it forces the preset's symbols on top of the
defaults and resolves the rest, with no copy into the kernel tree.

Equivalent bash, run inside the nixos-flake build devShell:

    make --directory="$worktree" O="$build_dir" KCONFIG_ALLCONFIG="$preset_file" alldefconfig
    make --silent --directory="$worktree" O="$build_dir" kernelrelease
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from f.common.devshell import DevShell, vendor_dir
from f.kernel.identity import bake_identity


def main(
    worktree: str,
    build_dir: str,
    preset: str = "imageless_defconfig",
    make_flags: str = "",
    build_identity: bool = True,
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    preset_file = _resolve_preset(workers, preset)
    build = Path(build_dir)
    build.mkdir(parents=True, exist_ok=True)

    # Same toolchain flags as the compile (LLVM=1 for clang) so config detection
    # matches the build; ccache/KBUILD_* are harmless here.
    flag_args = shlex.split(make_flags)
    shell = DevShell(workers)
    base = [f"--directory={worktree}", f"O={build}"]
    shell.run(
        "make", *base, *flag_args, f"KCONFIG_ALLCONFIG={preset_file}", "alldefconfig"
    )
    if build_identity:
        kernelrelease = bake_identity(shell, worktree, str(build), make_flags)
    else:
        kernelrelease = shell.capture(
            "make", "--silent", *base, *flag_args, "kernelrelease"
        ).strip()

    return {
        "kernelrelease": kernelrelease,
        "config": str(build / ".config"),
        "method": "preset",
        "preset": preset,
    }


def _resolve_preset(workers: Path, preset: str) -> Path:
    """Resolve a preset name to a file under the library, rejecting path escapes."""
    fragments = vendor_dir(workers) / "linux-config-fragments/defconfigs"
    candidate = (fragments / preset).resolve()
    if fragments.resolve() not in candidate.parents:
        raise ValueError(f"preset {preset!r} resolves outside {fragments}")
    if not candidate.is_file():
        have = ", ".join(p.name for p in sorted(fragments.glob("*"))) or "<none>"
        raise FileNotFoundError(
            f"preset {preset!r} not found in {fragments} (have: {have})"
        )
    return candidate
