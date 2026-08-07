# SPDX-License-Identifier: copyleft-next-0.3.1
"""Publish a kernel build's devel layer to the Nix store.

Runnable step, the devel-layer half of the Store transport and the companion to
`f/kernel/publish` (which publishes the run layer). Stage the part of the build dir a
worktree needs to re-index its source -- the kbuild command database (`*.cmd`), the
generated headers and sources (`*.h`, `*.c`, `*.rs`) and the kconfig files a Rust index
generator run reads (`rustc_cfg`, `auto.conf`, `.config`) -- by allowlist, so no
per-architecture image name (`Image`, `zImage`, `bzImage`, the `*.gz`/`*.zst`/...
variants) nor any other compiled output or link intermediate can leak in. The
host-tool build trees (`scripts/`, `tools/`) are dropped: the consuming worktree
carries its own source `scripts/` and regenerates with that. `f/common/store`'s
`publish_subset` stages that allowlist and adds it to the store under
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
- `*.rs`: the seven Rust sources a `CONFIG_RUST=y` build generates, 10,807,975 bytes
  (`rust/bindings/bindings_generated.rs` and `bindings_helpers_generated.rs`,
  `rust/uapi/uapi_generated.rs`, the three `rust/kernel/generated_arch_*_asm.rs`, and
  `rust/doctests_kernel_generated.rs`). The checked-in `rust/bindings/lib.rs`,
  `rust/uapi/lib.rs`, `rust/kernel/bug.rs` and `jump_label.rs` reach them through
  `include!(concat!(env!("OBJTREE"), ...))`, so without them rust-analyzer resolves
  15,560 declarations where it otherwise resolves 101,200. They are byte-identical
  across build paths. The glob sweeps exactly those seven and nothing else, because
  ADR-0003 keeps the build dir a separate child of the worktree, so no source `.rs`
  is ever in scope. It does carry 864,688 bytes of `doctests_kernel_generated.rs`
  that no index reads; keeping a file-type glob rather than a six-path list is
  deliberate, so a future upstream generated source ships with no allowlist edit.
- `rustc_cfg`: `include/generated/rustc_cfg`, 88,822 bytes, and the only objtree file
  `scripts/generate_rust_analyzer.py` opens (line 77). A consumer replaying that
  generator, the way it replays `gen_compile_commands.py` for the C index, has no
  other source for those cfg values.
- `auto.conf`: `include/config/auto.conf`, 37,727 bytes, which supplies `CONFIG_RUST`
  and `CONFIG_RUSTC_VERSION` to the sub-make that runs the generator.
  `scripts/Makefile.compiler:72` defines `rustc-min-version` as a test against
  `CONFIG_RUSTC_VERSION`, so with `auto.conf` absent the run still exits 0 and still
  writes a well-formed index, but `core-edition` silently falls back from 2024 to
  2021 and the `proc_macro2` crate loses `proc_macro_span_file` and
  `proc_macro_span_location`. It ships because that wrongness is silent.
- `.config`: 95,292 bytes, and not read by the index path at all. It is the layer's
  self-description, the input an opt-in proc-macro build needs, and the third of a
  trio the layer already half-ships, since `auto.conf.cmd` matches `*.cmd` today with
  neither of its two companions behind it.

Everything else is a compiled output (objects, archives, the image), a link
intermediate (`*.S` kallsyms, relocs) that clangd never indexes, or a host-tool build
artifact, none of which a source re-index reads. The three proc-macro dylibs
(`rust/libmacros.so`, `rust/libpin_init_internal.so`, `rust/libzerocopy_derive.so`)
are held out under that same rule: they are rustc-compiled and HOSTCC-linked, exactly
the compiled output this allowlist exists to exclude, and upstream states the coupling
at `rust/Makefile:605`, "Procedural macros can only be used with the `rustc` that
compiled it". The allowlist could not express them cleanly in any case, since a bare
`*.so` would sweep `arch/x86/entry/vdso/vdso64.so` in with them.

Returns the index `name`, the resolved `store_path`, and the `uts_release`.

Equivalent bash, the staged tree then added to the store:

    cd "$build_dir"
    find . -path ./scripts -prune -o -path ./tools -prune -o -type f \\
        \\( -name '*.cmd' -o -name '*.h' -o -name '*.c' -o -name '*.rs' \\
        -o -name rustc_cfg -o -name auto.conf -o -name .config \\) \\
        -exec cp --parents {} "$stage"/ \\;
    nix store add-path "$stage" --name kernel-devel-"$uts_release"
"""

from __future__ import annotations

from f.common import store

_DEVEL_KEEP = ("*.cmd", "*.h", "*.c", "*.rs", "rustc_cfg", "auto.conf", ".config")
_DROP_TREES = ("scripts", "tools")


def main(build_dir: str, uts_release: str) -> dict:
    name = f"kernel-devel-{uts_release}"
    sp = store.publish_subset(name, build_dir, _DEVEL_KEEP, _DROP_TREES)
    return {"name": name, "store_path": sp, "uts_release": uts_release}
