# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fetch the kernel devel layer onto a worktree and regenerate its clangd index.

The consumer-side companion to a build that ran on another host or worker: rsync the
build dir's developer subset — the `.cmd` files, generated headers, `Module.symvers`
and `scripts/`, but none of the object or image binaries — into this worktree's build
dir, then regenerate `compile_commands.json` locally so it indexes this worktree's
own source.

Same-host leaves `remote` empty (a local rsync); cross-host sets `remote` to an ssh
host and the source build dir is read over ssh. `build_dir` defaults to the worktree's
own `build` child and must stay under it.

Equivalent bash, run inside the nixos-flake transfer devShell:

    # excludes are the _BINARY_EXCLUDES binary patterns
    rsync --archive --no-owner --no-group --delete --delete-excluded \
        --exclude='*.o' --exclude='*.ko' ... \
        [<remote>:]"$src_build_dir"/ "$worktree/build"/
    python3 "$worktree/scripts/clang-tools/gen_compile_commands.py" \
        --directory "$worktree/build" --output "$worktree/compile_commands.json"
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from f.common.devshell import DevShell

# The devel layer is the build dir minus its binaries: keep the `.cmd` files,
# generated headers, `Module.symvers` and `scripts/`; drop objects and images.
_BINARY_EXCLUDES = (
    "*.o", "*.ko", "*.a", "*.o.d",
    "vmlinux", "vmlinux.o", "vmlinux.a", "vmlinux.unstripped", ".tmp_vmlinux*",
    "*.bin", "bzImage", "vmlinuz",
)


def main(
    worktree: str,
    src_build_dir: str,
    remote: str = "",
    build_dir: str = "",
) -> dict:
    wt = Path(worktree)
    gen = wt / "scripts/clang-tools/gen_compile_commands.py"
    if not gen.is_file():
        raise FileNotFoundError(f"no kernel source checkout at {wt}")
    build = Path(build_dir) if build_dir else wt / "build"
    if wt.resolve() not in build.resolve().parents:
        raise ValueError(
            f"build_dir {build} must live under the worktree {wt}: the fetched .cmd "
            "source paths are relative to the build dir, so only a child resolves them")
    build.mkdir(parents=True, exist_ok=True)

    src = src_build_dir.rstrip("/") + "/"
    if remote:
        src = f"{remote}:{src}"
    excludes = [f"--exclude={pattern}" for pattern in _BINARY_EXCLUDES]
    shell = DevShell(Path(os.environ["WORKERS_DIR"]), "transfer")
    shell.run("rsync", "--archive", "--no-owner", "--no-group", "--delete",
              "--delete-excluded", *excludes, src, str(build) + "/")
    print(f"fetched devel layer -> {build}", flush=True)

    cc = wt / "compile_commands.json"
    shell.run("python3", str(gen), "--directory", str(build), "--output", str(cc))
    entries = len(json.loads(cc.read_text())) if cc.is_file() else 0
    print(f"wrote {cc} ({entries} entries)", flush=True)

    return {
        "worktree": str(wt),
        "build_dir": str(build),
        "compile_commands": str(cc),
        "entries": entries,
        "remote": remote or None,
    }
