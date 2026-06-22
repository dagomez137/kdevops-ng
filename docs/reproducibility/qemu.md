# QEMU cross-host reproducibility + portability (v11.0.0, build-qemu devShell)

## Portability (build on B, use on A) — PROVEN
- Built QEMU x86_64-softmmu on host B (hetzie, 96c), installed to a destdir.
- Fetched the destdir to host A (hz-debian) at the SAME path (/home/dagomez/qemu-destdir
  -> datadir/firmware resolve, since both hosts share /home/dagomez).
- A ran it directly: "QEMU emulator version 11.0.0" (RPATH resolves to the nix-store
  closure A already has from the build-qemu devShell).
- A booted our reproducible kernel (built on B) with this B-built QEMU:
  "Linux version 7.1.0-rc7 ... SMP PREEMPT_DYNAMIC Sun Aug 25 ... 1991".
=> build on the powerful host, fetch + run on the thin one: YES, end to end.

## Reproducibility (beta1) — needs TWO fixes, same shape as the kernel
1. devShell fix (committed, flake 45d0dce): pin -frandom-seed + $out. Necessary but
   NOT sufficient.
2. -ffile-prefix-map=<source-root>=/qemu via configure --extra-cflags/--extra-cxxflags.
   The build dir is under the source root, so one map covers both source and build
   paths (debug + __FILE__). Maps the host-specific OLD prefix to a constant.

Without #2: A 37540b8f.. != B 44e666e2.. (205 source/build path strings differ;
~11k cascade strings from the path-length delta; size differs by 520 bytes).
With #2:    A e4919e8e.. == B e4919e8e.. byte-identical, build-id 1f267df0..,
            zero residual host-path strings.

meson confirms (meson/docs/markdown/Reproducible-builds.md): meson is reproducible
"assuming the rest of the build environment is set up for reproducibility" -- it does
NOT strip build paths itself (the 0.41 note is about meson's own determinism). The
environment must provide -ffile-prefix-map (as Debian does via dpkg-buildflags).

## Mapping to implementation (consumer side, currently deferred)
- Kernel: -fdebug-prefix-map=<worktree>/= in KCFLAGS/KAFLAGS (f/kernel/build_flags.py).
- QEMU:   -ffile-prefix-map=<source-root>=/qemu in --extra-cflags/--extra-cxxflags
          (f/qemu/configure.py). (Rust bits, if enabled, would also need
          --remap-path-prefix; not separately observed in this v11 build.)

## Flake gap found (separate from reproducibility)
build-qemu lacks `git`, which meson needs for git-based subproject wraps
(berkeley-softfloat-3, dtc, libblkio, ...) and which QEMU configure uses for its
version (git describe). On the production worker git likely leaks in from the
container base; the devShell does not guarantee it. Candidate follow-up: add
pkgs.git to build-qemu (b4 also wants git).

## Evidence
qemu-build.sh, qemu-build-a.sh, qemu-repro-build.sh, qemu-{B,A}.log,
qemu-repro-{A,B}.log
