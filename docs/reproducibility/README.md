# Cross-host build reproducibility — findings and remaining work

Empirical validation (2026-06-22) that Linux kernel **and** QEMU builds driven by the
`nixos-flake` `build-kernel`/`build-qemu` devShells are byte-identical across hosts,
and that the build-on-powerful-host / fetch-and-run-on-thin-host workflow works end to
end. This underwrites the content-addressed Store model (build identity ⇒ same bytes,
so fetch and rebuild are interchangeable).

Detail: [kernel.md](kernel.md), [qemu.md](qemu.md).

Tested on two machines: a Debian host (nix 2.34, 16c) and a NixOS host (nix 2.31, 96c).
The scripts and logs behind every result are working artifacts kept outside the repo at
`~/kernel/repro/` (host A) and `~/` (host B); they are not committed. The prior design
handoff (broader Store/workbench roadmap) is preserved at
`~/kernel/repro/DESIGN-HANDOFF-original.md`.

## Test status (all passed)
| Test | Proves | Result |
|---|---|---|
| R0 | same-host kernel determinism; isolate comp_dir | comp_dir leak → build-id cascade → bzImage; fixed |
| beta2 | toolchain parity from flake.lock | gcc/ld/ccache store paths identical across OS/nix version |
| beta1 kernel (tinyconfig) | fetch == rebuild | byte-identical vmlinux/bzImage/build-id |
| beta1 kernel (defconfig+modules) | all layers | run (bzImage + *.ko) + debug (vmlinux) + devel (symvers, .cmd) identical |
| alpha1 | mode-α LSP on fetched devel layer | compile_commands regenerated on consumer resolves, zero remap |
| QEMU portability | build on B, fetch + use on A | ran on A and booted the reproducible kernel |
| QEMU beta1 | QEMU reproducible cross-host | byte-identical with `-ffile-prefix-map` |
| R1 | build identity discriminates distinct builds | config/flags/toolchain/commit each change the hash; reaches `uname -r` + modules |

## Root causes (these changed the original plan)
1. **devShell `$out` leak (was unknown).** `pkgs.mkShell` derives `$out` from the
   caller's working directory, so stdenv injects a host-specific
   `-frandom-seed=hash($out)` into `NIX_CFLAGS_COMPILE` and `-L$out/lib` into
   `NIX_LDFLAGS`. Both reach the binary (DWARF producer, GNU build-id, `.rodata`). This
   was the dominant cross-host blocker for both kernel and QEMU.
2. **build-id cascade.** `--build-id` is a SHA1 over the output including debug
   sections, and the kernel embeds it in `.rodata`, so any debug-path/producer
   difference cascades into `bzImage`. The run layer is therefore not independently
   reproducible; the debug-path fixes are load-bearing for it too.
3. **Kernel `comp_dir`.** `DW_AT_comp_dir` is the absolute build dir. The fix is
   `-fdebug-prefix-map=<worktree>/=` — map the worktree prefix (not the build dir: GCC
   only remaps a parent prefix, and the build dir name is constant across hosts), with
   exactly one map (GCC consults the last-specified first; multiple maps fight).
4. **QEMU source/build paths.** meson uses absolute source paths; the fix is
   `-ffile-prefix-map=<source-root>=<const>` (one map covers source and build, since the
   build dir is under the source). meson does not strip paths itself — its docs state
   reproducibility holds "assuming the environment is set up", which means the
   environment must supply the prefix map (as Debian does).

## Fixes done (committed)
- `flake: reproducible kernel builds across hosts` — `build-kernel` `shellHook` pins
  `-frandom-seed` and rewrites the `$out` path in `NIX_CFLAGS_COMPILE`/`NIX_LDFLAGS`.
- `flake: reproducible QEMU builds across hosts` — shared `reproducibleShellHook`,
  applied to `build-qemu` too.

File: `vendor/nixos-flake/flake.nix`. Verified with `nix fmt`, `nix flake
check`, and a vanilla (no env hacks) cross-host build that came out byte-identical.

## Consumer-side fixes — implemented this session
1. **Kernel per-build path map** — `-fdebug-prefix-map=<commonparent>/=` in `KCFLAGS`
   and `KAFLAGS` (`f/kernel/build_flags.py`), one map over the common parent of the
   worktree and build dir so it is correct in both the sibling and child layouts.
   (Supersedes the original `T-fdebug-prefix-map`, whose `-fdebug-prefix-map=<build>=`
   spelling was wrong.)
2. **QEMU per-build path map** — `-ffile-prefix-map=<commonparent>=/qemu` in
   `--extra-cflags` and `--extra-cxxflags` (`f/qemu/configure.py`). Rust components, if
   enabled, also want `--remap-path-prefix`. The QEMU mode-α difference (EQ2: meson
   bakes absolute `-iquote` paths, so the consumer regenerates via a local
   `meson`/`configure` rather than fetch+regenerate-from-relative-`.cmd`) remains a
   Store-implementation detail.
3. **`build-qemu` git** — `pkgs.git` added to the `build-qemu` devShell so meson's
   git-based subproject wraps and `configure`'s `git describe` no longer rely on git
   leaking in from the container base.
4. **clangd outside the devShell — a non-issue.** Real clangd uses its own libclang
   resource dir, so it resolves a fetched worktree with no devShell and no `-isystem`
   baking; the alpha1 caveat was specific to running `gcc`, which clangd does not.
   Verified on six diverse TUs (`~/kernel/repro/alpha1-clangd.{sh,log}`).
5. **devel-layer fetch (mode α)** — `f/kernel/fetch_devel` rsyncs the devel subset
   (excludes `*.o *.ko *.a vmlinux* .tmp_vmlinux* *.bin bzImage vmlinuz`) into a
   worktree and regenerates `compile_commands.json` locally; validated end to end
   (3064 entries, clangd clean, zero remap).

Also implemented: the build dir is now a **child of the source worktree** (relative
`.cmd` paths, no remap), and the kernel **image installs versioned by release**
(`bzImage-<release>`, `System.map-<release>`), pairing with the already-versioned
modules so the destdir is an artefactory.

## Build identity / Store key (R1) — validated and implemented
R1 validated the key (`r1.sh`, `r1.log`): identity = `sha256(config[minus the
CONFIG_LOCALVERSION line] + toolchain + make flags + commit)[:12]`, injected into
`CONFIG_LOCALVERSION`. It is **deterministic**, **discriminates** on every input
(config, make flags, toolchain, commit each change the hash), the **regress-breaker
holds** (excluding the CONFIG_LOCALVERSION line keeps the hash stable when the digest
is injected), and it **reaches `uname -r` and modules** — two configs of the same
commit install as `/lib/modules/7.1.0-rc7-<hashA>` and `…-<hashB>`, no collision; the
image and modules share one identity.

Shipped in `f/kernel/identity` (`bake_identity`), called by the three configure steps
behind a `build_identity` knob (on by default). Two refinements over the R1 prototype:

- The **toolchain** id is the `build-kernel` devShell's `drvPath` — the literal
  "devShell store hash". It tracks the compiler, the reproducible `shellHook` and the
  pinned inputs, but (unlike hashing the `flake.nix`/`flake.lock` files) is *not*
  perturbed by adding an unrelated devShell, and is identical across hosts for one
  flake. The R1 prototype used `realpath $(command -v gcc)`, which misses the hook.
- The **make flags are host-path-normalized** (the `-fdebug-prefix-map=<worktree>/=`
  value is stripped) so the identity is the same on every host — the R1 prototype ran
  on a single host and did not exercise this.

The image and modules install under the unique release, so the destdir is a
content-addressed artefactory; what remains is the Store *structure* (a dir keyed by
identity, `run`/`debug`/`devel` layers, skip-rebuild-if-present, fetch-by-identity).

## Not yet tested (conditional / implementation-phase)
- **randstruct / module signing / `CONFIG_DEBUG_EFI`** — off in defconfig, so untested;
  if enabled they break reproducibility and need a pinned seed, a persistent signing
  key, and disabling the EFI debug paths respectively. Only matters if real configs
  enable them.
- **Store transport** — fetch-beats-build timing and NFS-co-located sharing; belongs to
  Store implementation, not a reproducibility experiment.
- **α2 full boot** — the reproducible kernel booted under the fetched QEMU (parsed
  cmdline); booting to userspace to confirm `uname -r == identity` and in-guest module
  load was not run.
