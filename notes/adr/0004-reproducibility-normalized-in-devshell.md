# Reproducibility is normalized in the Nix devShell, not per build

Cross-host build reproducibility was blocked by a Nix `pkgs.mkShell` behaviour:
`$out` is derived from the caller's CWD, so stdenv injects a host-specific
`-frandom-seed=hash($out)` into `NIX_CFLAGS_COMPILE` and `-L$out/lib` into
`NIX_LDFLAGS`, both of which reach the binary (DWARF producer, build-id, `.rodata`).
We fix this **in the `nixos-flake` devShell** via a shared `reproducibleShellHook`
that pins `-frandom-seed` and rewrites the `$out` paths, applied to both
`build-kernel` and `build-qemu` (commits `5e948d0`, `45d0dce`), rather than as
per-build environment hacks in each flow step. The per-build path maps (kernel
`-fdebug-prefix-map=<worktree>/=`, QEMU `-ffile-prefix-map=<source-root>=/qemu`)
remain the consumer-side companion, because they depend on the per-build path.

## Status

accepted

## Consequences

- The build *environment* is the unit of reproducibility; any flow that enters the
  devShell inherits the normalization, so individual steps need not re-pin seeds or
  store paths.
- This was the *actual* cross-host blocker: the per-build path map alone (the
  original `T-fdebug-prefix-map`) is necessary but not sufficient.
- Validated end to end on two real hosts (Debian/nix2.34 and NixOS/nix2.31):
  byte-identical kernel and QEMU, and a kernel/QEMU built on the beefy host booted
  on the thin host. Evidence and scripts: `~/kernel/repro/`.
- Follow-up gap: `build-qemu` lacks `git` (meson git-subproject wraps and QEMU
  `configure`'s `git describe` need it); add `pkgs.git`.
