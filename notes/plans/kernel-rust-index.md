# Plan: the Rust index on a developer worktree

Working note, 2026-08-07. **Executed.** All six steps are committed
(`bcd4451`, `7c5e4ea`, `34171ac`, `097550d`, `9e8eb9c`, `536d7b6`), both
workspaces are deployed, and the rxarray identity was rebuilt with reuse off
and verified: the layer carries the Rust inputs, both indexes land in the
worktree, and proc macros expand. The mission was to make `rust-project.json`
reach a developer worktree the way `compile_commands.json` already does, per
`notes/adr/0013-rust-index-regenerated-on-the-consumer.md`, including the
cross-host case.

Two things changed during execution and the record below has been left as
written rather than rewritten, so the reasoning stays legible. Step 4 was
planned as conditional and `proc_macros: False`; the editor observation (Q1)
made it unconditional and defaulted it on. A seventh piece of work, GC-rooting
the store paths the index names, was not planned at all and came out of the
design review. What remains open needs a peer host or a measurement nobody has
taken, and is listed in `notes/handoff/handoff-rust-index-replay.md`.

Everything below is measured, not estimated. The evidence came from a
non-destructive reproduction of a fresh consumer: a `git clone --shared`
of the Bare at the built commit plus the real devel layer
`kernel-devel-7.2.0-rc1-rxarray-v7.2-rc1-c9c7ddc7d625` materialized
exactly as `f/kernel/fetch_devel` materializes it. The user's worktree
at `~/wt/rxarray/linux` and the worker tree were read only.

## Current state (audited)

- `f/kernel/devtools.py:73` generates `rust-project.json` into the worker
  sandbox. Nothing downstream knows it exists.
- `f/kernel/publish_devel.py:46` allowlists `*.cmd`, `*.h`, `*.c`, so no
  Rust input ships. The developer's `build/rust/bindings/` is an empty
  directory.
- `f/kernel/fetch_devel.py:85` regenerates the C index only, in the
  `transfer` devShell.
- `f/qemu/publish_devel.py:59` already ships `rust-project.json` and
  `*.rs`, and `f/qemu/fetch_devel.py:55` already relocates the former.
  The kernel half is the outlier.

## Measured facts that drive the design

| Consumer state | Declarations resolved | Failure rates |
| --- | --- | --- |
| layer as published today | index cannot be generated | n/a |
| plus `.config` only | 15,560 | 41% / 44% |
| plus the seven generated `*.rs` | 101,200 | 4% / 4% |
| plus the three proc-macro dylibs | 104,659 | 4% / 4% |

Measured with `rust-analyzer analysis-stats`. The generated `*.rs` carry
the index; the dylibs are worth 3.3% by count, and qualitatively they are
`module!`, `#[vtable]`, `#[export]`, `fmt!`, `concat_idents!`, `paste!`,
`#[kunit_tests]`, `#[pin_data]`, `#[pinned_drop]`, `init!`, `pin_init!`
and the zerocopy derives.

Costs: the seven `*.rs` are 10,807,975 bytes, `rustc_cfg` is 88,822 and
`.config` is 95,292, so about +6% on a 175.9 MiB layer. `make rust/` is
47 seconds and about 206 MB; `make rust-analyzer` is 12.5 seconds cold
and 2 seconds warm; the generator invoked directly is under 2 seconds.

Two hazards the implementation must respect. Running any config-needing
goal without the builder's toolchain flags rewrites the developer's
`.config` from clang and LLD to GCC and BFD, adds `CONFIG_GCC_PLUGINS=y`,
and still emits a well-formed index naming the wrong sysroot. And the
`transfer` devShell has no Nix `rustc`, so `rustc` and `bindgen` leak
from the host PATH at 1.91.1 against the pinned 1.95.0.

## Sequence

Atomic commits, in order. Rule 6 applies to each: `nix flake check` and
`nix develop .#checks --command bash scripts/check-style.sh`.

1. **`kernel: ship the Rust index inputs in the devel layer`**
   `_DEVEL_KEEP` becomes `("*.cmd", "*.h", "*.c", "*.rs", "rustc_cfg",
   "auto.conf", ".config")`. No change to `f/common/store.py`: `subset_filter`
   fnmatches the basename, and `f/qemu/publish_devel` already mixes
   literals with globs. Extend the `Why each kept type` docstring block
   in its established what-it-is, who-consumes-it, what-breaks-without-it
   form, and rewrite the closing paragraph to name the proc-macro dylibs
   as the compiled output deliberately held out. Extend the exact-list
   fixture in `tests/test_kernel_common.py:237` so `.config`,
   `rustc_cfg` and a generated `.rs` survive while `rust/libmacros.so`
   and `arch/x86/entry/vdso/vdso64.so` are dropped. That last pair is
   what pins the invariant and proves `*.so` was never the mechanism.

2. **`kernel: treat a devel layer without .config as absent`**
   `f/kernel/reuse_check` probes `Path(devel, ".config").is_file()`, so an
   identity published before this contract is republished once instead of
   degrading forever. Not a new mechanism: that step's docstring already
   documents reading the run and devel layers apart for exactly this.

3. **`kernel: regenerate rust-project.json on the consumer`**
   The Rust half of `f/kernel/fetch_devel`, inline rather than a new step,
   because the step's concern is already "make this worktree indexable"
   and a separately skippable step is how a developer ends up with a
   half-red editor. Inline also leaves `f/kernel/build.flow` untouched, so
   `scripts/gen-bringup.py` regenerates nothing. Run it through
   `DevShell(workers)`, the `build-kernel` default, never `transfer`.
   Gate on `CONFIG_RUST=y` read from the materialized `.config`, exactly
   as `devtools.py:75` already does, and degrade with a printed reason
   rather than raising. Write the index through a sibling temp file and
   `os.replace`, borrowing `f/qemu/fetch_devel.py:177`, because a live
   rust-analyzer is reading that path. Print the resolved
   `proc_macro_dylib_path` count as data, derived by stat'ing the entries
   in the JSON just written rather than hardcoding three. The command, measured
   end to end at 1.1 seconds and verified to write only `rust-project.json`:

       make -f <wt>/scripts/Makefile.build obj=rust rust-analyzer \
           srcroot=<wt> srctree=<wt> objtree=<build> RUSTC=rustc

   Gate on `<build>/include/config/auto.conf` and
   `<build>/include/generated/rustc_cfg` both existing, and on `CONFIG_RUST=y`
   read from `auto.conf`. Never gate on the exit status: both missing-input
   cases exit 0 and write a plausible but wrong index.

4. **`kernel: offer the proc-macro build on a developer worktree`**
   Optional, and only if the handoff's editor observation says the
   dangling paths are disruptive. A `proc_macros: bool = False` script
   input running `make O=<build> <make_flags> rust/`, with a refusal when
   `.config` says `CONFIG_CC_IS_CLANG=y` and the flags are empty, because
   that is the measured corruption path. Script-level default with no
   schema property, so the bringup form does not grow.

5. **`notes: record ADR 0013 on the Rust index`** (this plan's ADR).

6. **`docs: document the Rust index on a developer worktree`**
   `docs/concepts/build-store.rst`: the devel-layer table row gains the
   Rust contents and names rust-analyzer beside clangd; the
   kbuild-versus-meson paragraph becomes two generators replayed rather
   than one; restate the layer size from a fresh measurement rather than
   adjusting the already-stale "roughly 190 MB".
   `docs/flows/kernel-build.rst`: the `publish_devel` and `fetch_devel`
   rows, and the `rust-analyzer.procMacro.server` setting, which is what
   actually makes proc macros expand. **Corrected in execution:** that last
   claim is false. Nothing needs setting, because rust-analyzer derives the
   server from the index's own `sysroot`, and setting it globally breaks
   every other Rust workspace on the machine. The shipped page documents it
   as a warning instead.
   Neither `clangd` nor `rust-analyzer` is registered in `docs/conf.py`'s
   `cmd_links` and every page writes both as plain literals today. Keep
   them plain and uniform: a half-conversion is the inconsistency to
   avoid, and an unregistered `:cmd:` name fails the build.

The Python in steps 1 through 4 goes to the python-expert subagent with a
tight spec, then comes back for review and the gate, per the established
preference for delegating step refactors.

## Deliberately out of scope

Each is real, each is pre-existing, and each is its own commit under rule
1. Do not fold them in.

- `f/kernel/fetch_devel.py:6` claims the layer carries `Module.symvers`
  and `scripts/`. Both are provably absent; `scripts` is in
  `_DROP_TREES`. The sentence gets rewritten by step 3, so correct it
  there or immediately after, but do not let the correction ride silently.
- `f/kernel/publish_devel.py:12` claims "the store path is identical on
  every host". It is not: 3,072 of the layer's 7,495 files embed the
  builder's absolute `WORKERS_DIR` path.
- The `.#build` devShell drift: nine citations in
  `docs/flows/kernel-build.rst` plus the stale comment at
  `f/common/devshell.py:258`. The real shells are `build-kernel`,
  `build-usertests`, `build-kselftests`, `build-qemu`, `transfer` and
  `systemd`.
- The absolute `build/vmlinux-gdb.py` symlink the layer ships with no
  relocation pass.
- A lean `index` devShell in `vendor/nixos-flake` (roughly 1.86 GiB
  against `build-kernel`'s 4.3 GiB). Deferred by the ADR until the
  mechanism is proven, because it drags in the subproject's own commit
  rules and a rollout across every worker host.
