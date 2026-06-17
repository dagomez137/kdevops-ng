# SPDX-License-Identifier: copyleft-next-0.3.1
"""Compile an already-configured kernel worktree.

Builds inside the nixos-flake build devShell with `make --jobs=$(nproc)` so the
container cgroup governs CPU and concurrent builds self-balance. `targets` is empty
by default, so a plain `make` builds the default goal — `vmlinux`, the arch boot
image (bzImage on x86), and, with CONFIG_MODULES, the modules — which is all the
install step needs. Pass explicit targets only to narrow the build.

Equivalent bash, run inside the nixos-flake build devShell:

    make --directory="$worktree" O="$build_dir" --jobs="$(nproc)" $make_flags $targets

The bzImage path is x86-only (`arch/x86/boot/bzImage` under the build dir); for
other arches/targets it does not exist and `image` is null.

Also returns everything the kernel records about the build in its generated headers
(rewritten by every make), each under the kernel's own macro name lowercased:
`uts_release` from `utsrelease.h`, `linux_compiler`/`uts_machine`/`linux_compile_by`/
`linux_compile_host` from `compile.h`, and `uts_version` (`uname -v`, with the
reproducible timestamp) from `utsversion.h`. These reflect what the build actually
used, so a mis-quoted input surfaces here rather than silently.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from f.common.devshell import DevShell

# include/generated macro -> result key. The key is the kernel's own macro name
# lowercased, verbatim (no renaming): utsrelease.h holds UTS_RELEASE, compile.h the
# LINUX_COMPILE*/UTS_MACHINE/LINUX_COMPILER, and utsversion.h's UTS_VERSION the
# timestamp (split out on modern kernels).
_BUILD_INFO = {
    "UTS_RELEASE": "uts_release",
    "LINUX_COMPILER": "linux_compiler",
    "UTS_MACHINE": "uts_machine",
    "LINUX_COMPILE_BY": "linux_compile_by",
    "LINUX_COMPILE_HOST": "linux_compile_host",
    "UTS_VERSION": "uts_version",
}
_DEFINE_RE = re.compile(r'#define\s+(\w+)\s+"(.*)"')


def _build_info(build: Path) -> dict:
    out = {key: None for key in _BUILD_INFO.values()}
    gen = build / "include/generated"
    for name in ("utsrelease.h", "compile.h", "utsversion.h"):
        path = gen / name
        if path.is_file():
            for macro, value in _DEFINE_RE.findall(path.read_text()):
                if macro in _BUILD_INFO:
                    out[_BUILD_INFO[macro]] = value
    return out


def main(
    worktree: str,
    build_dir: str,
    targets: str = "",
    make_flags: str = "",
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    build = Path(build_dir)

    # targets/make_flags are space-separated strings; split into argv elements
    # (multiple make goals / KFOO=bar flags) rather than concatenating.
    goal_args = shlex.split(targets)
    flag_args = shlex.split(make_flags)

    shell = DevShell(workers)
    shell.run(
        "make",
        f"--directory={worktree}",
        f"O={build}",
        f"--jobs={len(os.sched_getaffinity(0))}",
        *flag_args,
        *goal_args,
    )

    info = _build_info(build)
    print(f"built {info['uts_release']} with {info['linux_compiler']}", flush=True)

    # x86-only artifact path; non-x86/ARCH builds land elsewhere -> image is None.
    image = build / "arch/x86/boot/bzImage"
    if image.is_file():
        print(f"bzImage ready: {image} ({image.stat().st_size // 1024} KiB)", flush=True)
        return {"image": str(image), "targets": targets, **info}

    print(f"no bzImage at {image} (non-x86 target?)", flush=True)
    return {"image": None, "targets": targets, **info}
