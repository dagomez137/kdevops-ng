# SPDX-License-Identifier: copyleft-next-0.3.1
"""Configure method `make`: generate a `.config` from one or more config make goals.

Covers a single existing target (`defconfig`, `x86_64_defconfig`, `tinyconfig`, ...)
and ordered lists where `*.config` fragments merge onto a base, e.g.
`["defconfig", "kvm_guest.config"]`. Goals run in order.

Equivalent bash, run inside the nixos-flake build devShell:

    make --directory="$worktree" O="$build_dir" --jobs="$(nproc)" $make_flags $config_goals
    make --silent --directory="$worktree" O="$build_dir" kernelrelease
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from f.common.devshell import DevShell
from f.kernel.identity import bake_identity


def main(
    worktree: str,
    build_dir: str,
    defconfig: list[str] | None = None,
    make_flags: str = "",
    build_identity: bool = True,
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    build = Path(build_dir)

    # defconfig is a native list of config make goals applied in order; each goal
    # is its own argv element (word-split safe). Empty falls back to ["defconfig"].
    config_goals = [g for g in (defconfig or ["defconfig"]) if g]
    if not config_goals:
        raise ValueError("no config goals in defconfig")

    # make_flags is a space-separated string (e.g. "W=1" / KFOO=bar); split into argv.
    flag_args = shlex.split(make_flags)

    build.mkdir(parents=True, exist_ok=True)
    shell = DevShell(workers)
    base = [f"--directory={worktree}", f"O={build}"]
    shell.run("make", *base, f"--jobs={len(os.sched_getaffinity(0))}", *flag_args, *config_goals)
    if build_identity:
        kernelrelease = bake_identity(shell, worktree, str(build), make_flags)
    else:
        kernelrelease = shell.capture("make", "--silent", *base, *flag_args, "kernelrelease").strip()

    print(f"configured [{' '.join(config_goals)}] -> {kernelrelease or 'unknown'}", flush=True)

    return {
        "kernelrelease": kernelrelease or "unknown",
        "config": str(build / ".config"),
        "method": "make",
        "defconfig": config_goals,
    }
