# SPDX-License-Identifier: copyleft-next-0.3.1
"""Copy meson's compile_commands.json into the QEMU source root (optional, default on).

QEMU's build is meson-based, and meson already writes `compile_commands.json` into
the build dir as a side effect of configuring — there is no generator to run (unlike
the kernel, which runs gen_compile_commands.py over the .cmd files). The only thing
missing is its location: for an out-of-tree build, clangd indexes the source worktree
and looks for the index there, not in the separate build dir. So this step copies the
meson-generated index to the source root as a real, self-contained file (not a symlink
into a transient build dir), which is what clangd reliably picks up.

The index records absolute compile commands, so the copy still points clangd at the
build dir's objects regardless of where it is read from.

Equivalent bash:

    cp "$build_dir/compile_commands.json" "$worktree/compile_commands.json"
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def main(worktree: str, build_dir: str, compile_commands: bool = True) -> dict:
    if not compile_commands:
        return {"compile_commands": None}

    # meson writes this when it configures the build dir; nothing to generate.
    source = Path(build_dir) / "compile_commands.json"
    if not source.is_file():
        # The build still succeeded — this is just IDE tooling, so don't raise.
        print(f"no compile_commands.json in {build_dir}; skipping", flush=True)
        return {"compile_commands": None}

    # clangd indexes the source worktree, so place a real copy at its root. Write
    # to a temp file in the same dir and os.replace so a concurrent clangd never
    # reads a half-written index.
    dest = Path(worktree) / "compile_commands.json"
    tmp = dest.with_suffix(".json.tmp")
    shutil.copyfile(source, tmp)
    os.replace(tmp, dest)

    print(f"copied {source} -> {dest} ({dest.stat().st_size // 1024} KiB)", flush=True)
    return {"compile_commands": str(dest)}
