# SPDX-License-Identifier: copyleft-next-0.3.1
"""Compile an already-configured QEMU build.

QEMU's out-of-tree build is driven by `make` in the build dir, which in turn drives
ninja. Builds inside the nixos-flake build devShell with `make --jobs=$(nproc)` so
the container cgroup governs CPU and concurrent builds self-balance.

Equivalent bash, run inside the nixos-flake build devShell:

    make --directory="$build_dir" --jobs="$(nproc)"
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import DevShell


def main(build_dir: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])

    shell = DevShell(workers, "build-qemu")
    shell.run(
        "make",
        f"--directory={build_dir}",
        f"--jobs={len(os.sched_getaffinity(0))}",
    )

    print(f"compiled qemu build at {build_dir}", flush=True)
    return {"build_dir": build_dir}
