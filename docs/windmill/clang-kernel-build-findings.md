# Building the kernel with clang on nix — findings (clang gated, not yet supported)

The `f/kernel/build` flow gained a `compiler` knob (gcc | clang). **GCC works;
clang is gated out of the enum** because `LLVM=1` alone does not build the kernel
with the nixpkgs clang toolchain. This doc records exactly why, the proven recipe,
and what to implement to turn clang back on, so we can pick it up cold.

Status: **GCC + reproducible + ccache shipped and green; clang deferred.**

## What we tried and what happens

`build_flags` resolves `compiler=clang` to `LLVM=1` (plus `CC="ccache clang"` and the
reproducible `KBUILD_BUILD_*`). On the nixpkgs clang in the `#build` devShell that
fails in three distinct, layered ways — each confirmed by direct devShell repro:

1. **Wrapped clang injects `-nostdlibinc`.** The nixpkgs cc-wrapper adds
   `-nostdlibinc` (to redirect to nix's libc headers). The kernel compiles with
   `-nostdinc` + `-Werror=unused-command-line-argument`, so the wrapper's
   `-nostdlibinc` is "unused" and becomes a hard error — on the very first object
   (`scripts/mod/empty.o`). Hits **both** target (`CC`) and host (`HOSTCC`) compiles.

   ```
   clang: error: argument unused during compilation: '-nostdlibinc'
          [-Werror,-Wunused-command-line-argument]
   ```

2. **Unwrapped clang fixes the target but breaks host-tool linking.** Swapping to
   `llvmPackages.clang-unwrapped` makes target objects compile (the wrapper no longer
   injects `-nostdlibinc`), but host tools built with `HOSTCC=clang-unwrapped` link
   against the wrong libc and fail:

   ```
   ld: .../elfutils/lib/libelf.so: undefined reference to '__isoc23_strtol@GLIBC_2.38'
   ```

   (`tools/objtool` links `libelf`; unwrapped clang doesn't wire nix's glibc.)

3. **Unwrapped clang + the kernel's `-nostdinc` strips clang's own builtin headers.**
   Even host-aside, the target then can't find `stdarg.h`/`stddef.h` etc., because
   `-nostdinc` removes clang's resource dir and unwrapped clang doesn't re-add it.

Demoting the warning (`KCFLAGS`/`HOSTCFLAGS=-Wno-unused-command-line-argument`) is
whack-a-mole — silencing `-nostdlibinc` just surfaces the next unused arg
(`-Wa,--compress-debug-sections`), and never reaches a clean build.

## The proven recipe (what nixpkgs does)

nixpkgs' own kernel build does **not** use `LLVM=1`. It sets each tool explicitly
(`pkgs/os-specific/linux/kernel/common-flags.nix`):

- `CC` = **unwrapped** clang (`stdenv.cc.cc`; comment: *"the clang-wrapper doesn't
  like -target"*).
- `HOSTCC` / `HOSTCXX` = **wrapped** clang (`buildPackages.stdenv.cc`) — host tools
  need nix's libc to link.
- `LD` = the **unwrapped** linker (the bintools *wrapper* for ld.lld breaks kernel
  links — nixpkgs#321667). Our `pkgs.lld` already provides a raw `ld.lld`.
- `AR`/`NM`/`STRIP`/`OBJCOPY`/`OBJDUMP`/`READELF`, and `HOSTAR`/`HOSTLD`.
- For clang: `CFLAGS_KERNEL` and `CFLAGS_MODULE` = `-I${clangLib}/lib/clang/${major}/include`
  where `clangLib = lib.getLib stdenv.cc.cc` (the clang-unwrapped **`lib`** output —
  *not* its `out` path; the `out/lib/clang/<ver>/include` we first tried is empty)
  and `major = lib.versions.major clangLib.version`.

A devShell repro of `CC=<unwrapped> HOSTCC=<wrapped> CFLAGS_KERNEL=-I<lib-resource>
LLVM=1` builds target + host objects cleanly; the only thing that bit us last was
using the wrong (`out`) output for the resource include.

## What to implement to enable clang

The store paths above are nix-internal, so the clean home is the **nixos-flake**, not
`build_flags` (which runs outside the devShell and can't compute them):

1. In `workers/shared/nixos-flake`, expose the LLVM kernel make-flags string,
   computed in nix from `llvmPackages.clang-unwrapped` (CC + resource `-I` from its
   `lib` output), the wrapped clang (`HOSTCC`/`HOSTCXX`), and `lld` (`LD`). Either a
   flake output (`packages.<sys>.kernelLlvmFlags`, a text file) or a devShell env var
   (e.g. `KERNEL_LLVM_FLAGS`).
2. In `f/kernel/build_flags`, when `compiler=clang`, splice those flags in instead of
   `LLVM=1`. If it's a devShell env var, the make steps expand it (devShell-controlled,
   no user input), e.g. via a small `sh -c 'exec make "$@" $KERNEL_LLVM_FLAGS'`
   wrapper in `DevShell`; if it's a flake output, `build_flags` reads it with
   `nix build/eval --print-out-paths` and appends it.
3. Re-add `clang` to the `compiler` enum in `build.flow` + `build_flags.script.yaml`.
4. ccache composes: `CC="ccache <unwrapped-clang>"` (target), leave `HOSTCC` wrapped.

The `build_flags` clang branch already emits the right *shape* (the issue is purely
the nix toolchain), so most of the change is in the nixos-flake.

## What shipped instead

`compiler=gcc` (default), plus `reproducible` (fixed `KBUILD_BUILD_TIMESTAMP` =
`Sun Aug 25 20:57:08 UTC 1991`, `KBUILD_BUILD_USER/HOST=kdevops`, `LOCALVERSION=`) and
`ccache` (hermetic nix ccache, `CC="ccache gcc"`, shared `CCACHE_DIR`) — all on by
default and verified green. See `f/kernel/build_flags`.

Note on the reproducible timestamp: kdevops sets `KBUILD_BUILD_TIMESTAMP=''`, which is
**not** reproducible — the kernel does `build-timestamp = $(or $(KBUILD_BUILD_TIMESTAMP),
$(shell date))` (`init/Makefile`), so an empty value falls back to live `date`. We use a
real fixed constant; `timestamp_from_commit` switches it to the commit date.
