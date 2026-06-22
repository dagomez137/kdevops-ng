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

File: `workers/shared/nixos-flake/flake.nix`. Verified with `nix fmt`, `nix flake
check`, and a vanilla (no env hacks) cross-host build that came out byte-identical.

## Fixes still needed
1. **Kernel per-build path map** — add `-fdebug-prefix-map=<worktree>/=` to `KCFLAGS`
   and `KAFLAGS` in `f/kernel/build_flags.py`, gated on `reproducible`, keyed on the
   worktree path. (Supersedes the original `T-fdebug-prefix-map`, whose
   `-fdebug-prefix-map=<build>=` spelling was wrong.)
2. **QEMU per-build path map** — add `-ffile-prefix-map=<source-root>=<const>` to
   `--extra-cflags` and `--extra-cxxflags` in `f/qemu/configure.py`. Rust components, if
   enabled, also want `--remap-path-prefix`.
3. **`build-qemu` lacks `git`** — meson needs it for git-based subproject wraps and
   QEMU `configure` uses `git describe`. Add `pkgs.git` to `build-qemu` (b4 wants it
   too). On the production worker git currently leaks in from the container base.
4. **clangd outside the devShell** — `NIX_CFLAGS_COMPILE` carries the `-isystem` nix
   paths as environment, not in the `.cmd`; the alpha1 test ran the recorded command
   inside the devShell. Decide whether real clangd runs in the devShell or whether
   `compile_commands.json` must bake the `-isystem` paths.
5. **devel-layer fetch set (mode α)** — exclude all binaries (`*.o *.ko *.a vmlinux
   vmlinux.unstripped .tmp_vmlinux* *.bin bzImage`); the real devel layer is ~200M for
   defconfig, dominated by `.cmd` (~174M).

## Not yet tested
- **Identity discrimination** — flip one input (KASAN, gcc↔clang, a `KCFLAGS`) and
  confirm a different build identity / `uname -r` / `/lib/modules/<rel>/`.
- **randstruct / module signing / `CONFIG_DEBUG_EFI`** — off in defconfig, so untested;
  if enabled they break reproducibility and need a pinned seed, a persistent signing
  key, and disabling the EFI debug paths respectively.
- **localversion** — not the beta1 blocker (both builds were at the exact tag, no `+`).
  A shallow-clone `setlocalversion` `+` is a latent hazard; the substantive work is
  baking the build-identity hash into `kernelrelease`.
