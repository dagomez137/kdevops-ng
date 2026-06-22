# SPDX-License-Identifier: copyleft-next-0.3.1
"""Publish a kernel build's devel layer to the Nix store.

Runnable step, the devel-layer half of the Store transport and the companion to
`f/kernel/publish` (which publishes the run layer). Stage the part of the build dir a
worktree needs to re-index its source -- the kbuild command database (`*.cmd`) and the
generated headers and sources (`*.h`, `*.c`) -- by allowlist, so no per-architecture
image name (`Image`, `zImage`, `bzImage`, the `*.gz`/`*.zst`/... variants) nor any
other compiled output or link intermediate can leak in. The host-tool build trees
(`scripts/`, `tools/`) are dropped: the consuming worktree carries its own source
`scripts/` and regenerates with that. Add the result to the store under
`kernel-devel-<release>`; the store path is identical on every host, so a peer can
fetch it by release with `nix copy`.

Why each kept type, the `_DEVEL_KEEP` allowlist:

- `*.cmd`: the kbuild command database, and 90%+ of the layer. One `.<obj>.cmd` per
  object holds that translation unit's exact compiler command line and the full list of
  headers it included; `gen_compile_commands.py` turns these into
  `compile_commands.json`, the per-file command clangd replays. They are the index.
- `*.h`: generated headers absent from the source tree (`autoconf.h` with every
  `CONFIG_*`, `asm-offsets.h`, syscall and instruction tables). Kernel source
  `#include`s them, so without them clangd cannot resolve those includes or `CONFIG_*`
  and floods every file with false errors.
- `*.c`: generated translation units that carry their own `.cmd` (`inat-tables.c`,
  `.vmlinux.export.c`); clangd opens them when it walks their compile command.

Everything else is a compiled output (objects, archives, the image), a link
intermediate (`*.S` kallsyms, relocs) that clangd never indexes, or a host-tool build
artifact -- none of which a source re-index reads.

Returns the index `name`, the resolved `store_path`, and the `uts_release`.

Equivalent bash, the staged tree then added to the store:

    cd "$build_dir"
    find . -path ./scripts -prune -o -path ./tools -prune -o -type f \\
        \\( -name '*.cmd' -o -name '*.h' -o -name '*.c' \\) -exec cp --parents {} "$stage"/ \\;
    nix store add-path "$stage" --name kernel-devel-"$uts_release"
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import tempfile
from pathlib import Path

from f.common import store

_DEVEL_KEEP = ("*.cmd", "*.h", "*.c")
_DROP_TREES = ("scripts", "tools")


def _stage_filter(build_dir: str):
    """A `copytree` ignore that keeps only `_DEVEL_KEEP` files, minus `_DROP_TREES`."""
    root = os.path.realpath(build_dir)

    def ignore(dirpath: str, names: list[str]) -> list[str]:
        at_root = os.path.realpath(dirpath) == root
        drop = []
        for n in names:
            full = os.path.join(dirpath, n)
            if os.path.islink(full):
                continue
            if os.path.isdir(full):
                if at_root and n in _DROP_TREES:
                    drop.append(n)
                continue
            if not any(fnmatch.fnmatch(n, pat) for pat in _DEVEL_KEEP):
                drop.append(n)
        return drop

    return ignore


def main(build_dir: str, uts_release: str) -> dict:
    name = f"kernel-devel-{uts_release}"

    tmp = Path(tempfile.mkdtemp(prefix=f"{name}-"))
    try:
        stage = tmp / name
        shutil.copytree(build_dir, stage, symlinks=True,
                        ignore=_stage_filter(build_dir))
        print(f"staged devel layer -> {stage}", flush=True)
        sp = store.publish(name, str(stage))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {"name": name, "store_path": sp, "uts_release": uts_release}
