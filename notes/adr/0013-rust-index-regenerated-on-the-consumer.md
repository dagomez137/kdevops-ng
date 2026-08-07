# The Rust index is regenerated on the consumer

`f/kernel/devtools` generates `rust-project.json` beside `compile_commands.json`
and the GDB helpers, but only the C index ever reaches a developer. The devel
layer's allowlist is `*.cmd`, `*.h`, `*.c`, so nothing Rust is published, and
`f/kernel/fetch_devel` regenerates only the C index. A developer who asks for a
worktree gets a working clangd index and no rust-analyzer index at all, even
though ADR-0010 already promised that a group worktree exists "purely to receive
a build's devel artifacts for clangd, rust-analyzer and GDB work".

The operative rule is not "never remap". It is the one commit `64e471c` wrote
down when it added the QEMU layer: if the build system leaves a replayable
generator input, ship the input and replay it on the consumer; relocate only
where no generator exists. kbuild leaves `scripts/generate_rust_analyzer.py` and
drives it from the `rust-analyzer` target, so the kernel's Rust index falls on
the regenerate side, exactly as its C index does.

Regeneration is not a preference here, it is forced. `rust/Makefile` invokes the
generator as `$(realpath $(srctree)) $(realpath $(objtree))`, so every path in
the result is absolute by construction and ADR-0003's relative-`srctree` trick
never reaches it: 42 of 46 crate roots name the builder's worktree, and the
build dir is baked into each generated crate's `env.OBJTREE`, its
`source.include_dirs` and its `proc_macro_dylib_path`. There is no upstream flag
that produces a relocatable one. QEMU's textual relocation has no analogue
either, because 4 of the 46 crates root under a toolchain store path that the
`dirname(entries[0]["directory"])` anchor cannot reach.

The generator is far cheaper than the target that wraps it. It reads the objtree
in exactly two places, `include/generated/rustc_cfg` and an `objtree.resolve()`
for the `OBJTREE` variable, and it reads no `.config` and no `CONFIG_` symbol.
The `.config` requirement belongs to the `make rust-analyzer` goal alone, which
is absent from `no-dot-config-targets` and therefore drags in kconfig. Running a
config-needing goal with the wrong toolchain flags was measured to rewrite the
developer's `.config` from clang and LLD to GCC and BFD, add
`CONFIG_GCC_PLUGINS=y`, and still emit a well-formed index naming the wrong
sysroot.

So the consumer skips the top-level goal and enters the same sub-make the goal
would have entered, `make -f scripts/Makefile.build obj=rust rust-analyzer`.
That file soft-includes `auto.conf` rather than requiring it, so no kconfig ever
runs, `.config` is never read, and the whole class of kconfig hazards becomes
structurally impossible rather than something to guard against. Measured on a
freshly materialized layer: 1.1 seconds, exit 0, and exactly one file written,
`rust-project.json`, whose content is path-normalized identical to the builder's
across all 46 crates including the sysroot.

The layer must therefore carry `auto.conf`, and this is the subtle part.
`rust/Makefile` gates several cfg values on `rustc-min-version`, which
`scripts/Makefile.compiler:72` defines as a test against `CONFIG_RUSTC_VERSION`,
a kconfig symbol. With `auto.conf` absent the sub-make still exits 0 and still
writes a well-formed index, but `core-edition` silently falls back from 2024 to
2021 and the `proc_macro2` crate loses `proc_macro_span_file` and
`proc_macro_span_location`. That is the same silent-wrongness the `transfer`
devShell produces, reached by a different road, and it is why the consumer gates
on `auto.conf` and `rustc_cfg` being present rather than trusting an exit code.

The three proc-macro dylibs are held out of the layer, and the reason is not
their size. Upstream states the coupling directly at `rust/Makefile:605`,
"Procedural macros can only be used with the `rustc` that compiled it", and the
contract is an exact version-string match. They are also not byte-reproducible
across build paths, and the allowlist cannot express them without enumerating
rustc-derived basenames, since `*.so` would sweep `arch/x86/entry/vdso/vdso64.so`.
So the consumer compiles them instead, in the same devShell that regenerates the
index.

That the consumer compiles them rather than doing without was decided by
observation, after this ADR was first written. The original reasoning treated
the dylibs as worth only 3.3% of resolved declarations and therefore optional.
In a real editor the absence is not a 3.3% shortfall, it is a `macro-error`
diagnostic at every `module!`, `#[vtable]`, `#[kunit_tests]` and `pin_init!`
site, so a developer worktree came up with its Rust code visibly red. A
developer worktree exists to give an editor that works, so `proc_macros`
defaults on.

Nothing needs to be told where the matching server is, and this is the part
that makes the arrangement reproducible rather than merely correct.
`rust-analyzer` reads the `sysroot` field of `rust-project.json` and starts
`<sysroot>/libexec/rust-analyzer-proc-macro-srv` itself. Because one devShell
writes both that field and the dylibs, they move in lockstep on every toolchain
bump, with no configuration to drift. Measured with a rust-analyzer 1.91.1
client against a 1.95.0 toolchain and no configuration whatsoever: 561 modules
and 104,660 declarations, the complete index. An explicit
`rust-analyzer.procMacro.server` is therefore an override of a value already
carried correctly, and in a global editor configuration it is harmful: it
applies to every Rust workspace, and it was measured breaking proc-macro
expansion in an ordinary Cargo project whose crates a different `rustc` had
built.

## What the layer carries

The four additions and why each one is source rather than output:

| Entry | Bytes | Why |
| --- | --- | --- |
| `*.rs` | 10,807,975 | The seven generated sources the index resolves. Byte-identical across build paths, and `f/qemu/publish_devel` already carries this same glob for the same reason. |
| `rustc_cfg` | 88,822 | The generator's single mandatory objtree read. |
| `auto.conf` | 37,727 | Supplies `CONFIG_RUST` and `CONFIG_RUSTC_VERSION` to the sub-make. Without it the cfgs are silently wrong. |
| `.config` | 95,292 | Not read by the index path at all. It is the layer's self-description and the input the opt-in proc-macro build needs, and it completes a trio the layer already half-ships, since `auto.conf.cmd` matches `*.cmd` today with neither of its two companions behind it. |

That is about +6% on a measured 175.9 MiB layer. `*.rs` sweeps exactly the seven
generated files and nothing else, since ADR-0003 pins the build dir under the
worktree so no source `.rs` is ever in scope. It carries 864,688 bytes of
`doctests_kernel_generated.rs` that the index never reads; keeping the file-type
glob rather than a six-path list is deliberate, so a future upstream generated
source ships with no allowlist edit.

## Status

accepted

## Considered Options

- **Copy the builder's `rust-project.json` into the layer.** Rejected: 42 of 46
  crate roots and every `OBJTREE`, `include_dirs` and `proc_macro_dylib_path`
  name the builder, and `$(realpath ...)` makes that unconditional.
- **Relocate it textually, as `f/qemu/fetch_devel` does.** Rejected: four crates
  root under a toolchain store path with no anchor to substitute from, so the
  QEMU relocator's mechanism does not carry over.
- **Ship `.config` only and rebuild everything on the consumer with
  `make O=<build> rust/`.** Rejected as the default: measured at 47 seconds and
  about 206 MB inside the developer's own worktree, it needs the builder's full
  make flags to avoid a `-nostdlibinc` failure, and it buys 3.3% of the index by
  declaration count. Kept as an opt-in for the driver-authoring case.
- **Ship the three proc-macro dylibs.** Rejected: they are rustc-compiled and
  HOSTCC-linked, the compiled output `_DEVEL_KEEP` exists to exclude; they are
  not byte-reproducible across build paths; the allowlist cannot express them
  without enumerating rustc-derived basenames, since `*.so` would sweep
  `arch/x86/entry/vdso/vdso64.so`; and shipping fixes nothing that compiling
  locally does not, since either way the dylib loads only into a server built
  by the same `rustc`.
- **Configure the editor to name the pinned proc-macro server.** Rejected on
  measurement. It is unnecessary, because `rust-analyzer` derives the server
  from the index's own `sysroot`; it pins a store path that the next toolchain
  bump invalidates; and in a global editor configuration it breaks every other
  Rust workspace on the machine. Documented as a warning rather than advice.
- **A devShell, or a generated editor configuration, to pin the developer's
  language tooling.** Rejected as solving a problem that does not exist. The
  reproducibility is already structural: one devShell writes both the dylibs
  and the `sysroot` the editor follows. A generated per-worktree
  `.helix/languages.toml` was the strongest rival and would not have dirtied
  the tree, since the kernel's `.gitignore` ignores all dotfiles, but it could
  only transcribe a value the arrangement already derives.
- **Run the top-level `make rust-analyzer` goal on the consumer.** Rejected.
  It works, measured at 12.5 seconds cold with the `.config` left byte-pristine
  once `LLVM=1` is derived from `CONFIG_CC_IS_CLANG=y`, and it has the merit of
  using kbuild's documented public interface rather than the internal
  `$(build)=` one. But it is one forgotten token away from the measured silent
  corruption, and it buys that risk nothing: the sub-make produces a
  byte-identical index in a tenth of the time and cannot reach kconfig at all.
  The cost of the chosen route is a coupling to `scripts/Makefile.build`'s
  calling convention, which is the trade the fixture tests exist to catch.
- **Regenerate in the `transfer` devShell, where `fetch_devel` runs today.**
  Rejected: that shell is `nix`, `openssh` and `python3`, so `rustc` and
  `bindgen` leak in from the host PATH at a different version and
  `RUST_LIB_SRC` is unset. Measured, it produced a plausible-looking index
  naming `~/.rustup` and corrupted the `.config` on the way.
- **Add a lean `index` devShell to the vendored flake.** Deferred, not rejected.
  `build-kernel` already carries `rustc` and `RUST_LIB_SRC`, so the correctness
  win lands with no vendored-subproject commit and no rollout sequencing across
  worker hosts. Revisit once the mechanism is proven.
- **Ship the generator's inputs and replay it on the consumer** (chosen). One
  allowlist change, no new transport, and the direct sibling of the sentence
  that already describes the C index.

## Consequences

- `f/kernel/fetch_devel` gains a Rust half and therefore a second devShell: the
  `transfer` shell keeps the `nix copy` and materialize work it already does,
  and the regeneration runs in `build-kernel`. Two shells in one step is
  existing practice; `f/kernel/publish` already enters `transfer` through
  `f/common/store`.
- The Rust half degrades honestly and never raises. A layer published before
  this contract, a kernel without `CONFIG_RUST=y`, and a kernel too old to carry
  the generator each print their reason and leave `rust_project` unset. A
  missing Rust index must not cost the developer their C index.
- The gate is a presence check on `auto.conf` and `rustc_cfg`, not an exit
  status. Both missing-input failures were measured to exit 0 and write a
  plausible index, so the step must refuse to run rather than inspect the
  result. `CONFIG_RUST=y` is read from `auto.conf`, which the sub-make needs
  anyway, so the index path never opens `.config`.
- The step writes exactly one file into the developer's build dir. That was
  measured, not assumed: the run leaves `auto.conf`, `rustc_cfg` and the
  generated sources byte-identical and creates no `.config`.
- The step prints how many `proc_macro_dylib_path` entries resolved, because the
  generator writes those paths without stat'ing them, so a partial index is
  otherwise indistinguishable from a complete one in the job log.
- Cross-host needs no new mechanism. `store.resolve` already runs the
  local-then-peer cascade and `fetch_devel` already exposes `remote` and
  `remote_index`. The layer is fixed-output content-addressed with no
  references, and regeneration is purely local: no crate roots under the build
  dir, and `sysroot` is a runtime probe of the consumer's own rustc rather than
  a recorded value. A peer on a different pin still gets a self-consistent
  index.
- The consumer must be able to enter the pinned `build-kernel` devShell from the
  vendored flake. That is a real precondition on a developer host, and it is the
  cost of not having a lean index shell yet.
- Proc-macro expansion needs the dylibs present and nothing else. The consumer
  compiles them, `rust-analyzer` finds the matching server through the index,
  and no editor setting is involved on any correct path.
- The index names store paths it does not own, so the step GC-roots them. The
  `sysroot` and the `rust-lib-src` behind four of the crates are referenced by
  the devShell as an environment variable rather than as packages, so nothing
  else roots them: `rust-lib-src` was measured at zero GC roots, one
  `nix store gc` away from breaking every kernel Rust index on the host. The
  roots live outside the Store index, which is the identity catalog and has no
  business holding a toolchain path.
- `f/kernel/reuse_check` treats a devel layer with no `.config` as absent, so an
  identity built before this contract is republished once instead of being
  indexed forever with no Rust half.
