# SPDX-License-Identifier: copyleft-next-0.3.1
"""Publish a QEMU build's devel layer to the Nix store.

Runnable step, the devel-layer half of the Store transport and the companion to
`f/qemu/publish` (which publishes the run layer, the install prefix). Stage the part of
the build dir a worktree needs to index its source, meson's own index plus the
generated headers and sources, by allowlist, so none of the objects, archives and built
emulators around them can leak in. `f/common/store`'s `publish_subset` does the staging
and adds the result under `qemu-devel-<version>-<label>-<identity>`, keyed on the
install prefix's basename exactly as the run layer is; the store path is identical on
every host, so a peer can fetch it with `nix copy`.

Why each kept type, the `_DEVEL_KEEP` allowlist:

- `compile_commands.json`: meson writes the index itself as a side effect of
  configuring, so the layer ships a finished index. This is the structural difference
  from the kernel: kbuild leaves a `.cmd` database that `gen_compile_commands.py`
  replays, so `f/kernel/publish_devel` ships the database and `f/kernel/fetch_devel`
  regenerates the index locally. Meson leaves no such database, so `f/qemu/fetch_devel`
  relocates this index's recorded paths instead.
- `*.h`, `*.h.inc`, `*.def`: generated headers absent from the source tree
  (`config-host.h`, `config-poison.h`, `qapi/*.h`, `trace/*.h`, `hmp-commands.h`,
  `qemu-options.def`). QEMU source `#include`s them, so without them clangd cannot
  resolve those includes and floods every file with false errors.
- `*.c`, `*.c.inc`: generated translation units the index names (`qapi-*.c`,
  `trace/*.c`, the `ui/input-keymap-*.c.inc` tables); clangd opens them when it walks
  their compile command.
- `rust-project.json`, `*.rs`: rust-analyzer's index and the generated bindings an
  `--enable-rust` build leaves. They ship when the build produced them.

Symlinks ship whatever their name (`publish_subset` keeps every link), because
`build/linux-headers/asm` is how the relative `-isystem linux-headers` include reaches
the target's headers; `f/qemu/fetch_devel` re-points them at the consuming worktree.

The two dropped trees are not source: `qemu-bundle` is a tree of symlinks aliasing the
install prefix's binaries and `pyvenv` is meson's Python virtualenv. Everything else
falls out by type, the objects (`*.o`), the dep files (`*.o.d`), the archives, the
built emulators, and the Sphinx `docs/` output.

Returns the index `name`, the resolved `store_path`, and the `prefix`.

Equivalent bash, the staged tree then added to the store:

    cd "$build_dir"
    find . -path ./qemu-bundle -prune -o -path ./pyvenv -prune -o -type f \\
        \\( -name compile_commands.json -o -name rust-project.json \\
        -o -name '*.h' -o -name '*.h.inc' -o -name '*.def' \\
        -o -name '*.c' -o -name '*.c.inc' -o -name '*.rs' \\) \\
        -exec cp --parents {} "$stage"/ \\;
    nix store add-path "$stage" --name qemu-devel-"$(basename "$prefix")"
"""

from __future__ import annotations

from pathlib import Path

from f.common import store

_DEVEL_KEEP = (
    "compile_commands.json",
    "rust-project.json",
    "*.h",
    "*.h.inc",
    "*.def",
    "*.c",
    "*.c.inc",
    "*.rs",
)
_DROP_TREES = ("qemu-bundle", "pyvenv")


def main(build_dir: str, prefix: str) -> dict:
    name = f"qemu-devel-{Path(prefix).name}"
    sp = store.publish_subset(name, build_dir, _DEVEL_KEEP, _DROP_TREES)
    return {"name": name, "store_path": sp, "prefix": prefix}
