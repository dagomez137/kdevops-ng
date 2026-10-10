# SPDX-License-Identifier: copyleft-next-0.3.1
"""The NVIDIA open GPU kernel modules built with a kernel.

Library module (not a step), shared by `build_nvidia` and `reuse_check`. The
source is the vendored nixos-flake's `nvidia-open-src`, the release of the
driver userspace its `gpu` profile ships. nvidia.ko refuses a userspace of any
other version, so the modules installed with a kernel come from exactly that
release, and a run layer holding another release does not count as built.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from f.common.devshell import DevShell, Nix, vendor_dir

# nvidia.ko exports dma-bufs and nvidia-uvm imports them.
REQUIRED_CONFIG = ("CONFIG_MODULES", "CONFIG_DMA_SHARED_BUFFER")

# The make variables that pick the kernel's compiler. Everything else a kernel
# build passes (warning levels, KCFLAGS, build stamps) is about the kernel.
_TOOLCHAIN = ("LLVM=", "CC=", "CFLAGS_MODULE=")


def _flake(workers: Path) -> str:
    return f"path:{vendor_dir(workers)}/nixos-flake#nvidia-open-src"


def source(workers: Path) -> tuple[str, str]:
    """The module source in the store and the driver release it is."""
    ref = _flake(workers)
    path = Nix().out_path(ref)
    version = Nix().capture("eval", "--raw", f"{ref}.version").strip()
    return path, version


def missing_config(build_dir: str) -> list[str]:
    """The REQUIRED_CONFIG symbols the kernel's .config does not set to y."""
    lines = set(Path(build_dir, ".config").read_text().splitlines())
    return [sym for sym in REQUIRED_CONFIG if f"{sym}=y" not in lines]


def toolchain_flags(make_flags: str) -> list[str]:
    """The kernel build's GCC toolchain, for building modules against it.

    The build shell's default C and C++ compilers are Clang's, so the GCC that
    built the kernel is named for both; NVIDIA compiles some sources with CXX.
    """
    flags = [f for f in shlex.split(make_flags) if f.startswith(_TOOLCHAIN)]
    cc = next((f[len("CC=") :] for f in flags if f.startswith("CC=")), "gcc")
    if "clang" in cc or any(f.startswith("LLVM=") for f in flags):
        raise ValueError(
            "the NVIDIA modules are built and tested with GCC kernels only; "
            "this kernel builds with Clang"
        )
    if not any(f.startswith("CC=") for f in flags):
        flags.append("CC=gcc")
    flags.append("CXX=g++")
    return flags


def refuse_unsupported(build_dir: str, make_flags: str) -> None:
    """Raise ValueError when this kernel cannot carry the NVIDIA modules."""
    toolchain_flags(make_flags)
    missing = missing_config(build_dir)
    if missing:
        raise ValueError(
            f"the NVIDIA modules need {', '.join(missing)}=y in the kernel's "
            ".config; the imageless_defconfig preset sets them, and so does the "
            "virt/gpu-passthrough fragment"
        )


def installed_version(root: str, uts_release: str, workers: Path) -> str | None:
    """The release of the nvidia.ko installed under `root` for this kernel."""
    found = sorted(Path(root, "lib/modules", uts_release).rglob("nvidia.ko*"))
    if not found:
        return None
    version = DevShell(workers).capture(
        "modinfo", "--field=version", str(found[0]), check=False
    )
    return version.strip() or None


def main():
    """Library module imported by f/kernel steps; not a runnable step."""
    return "f/kernel/nvidia: NVIDIA open GPU module source and install probe"
