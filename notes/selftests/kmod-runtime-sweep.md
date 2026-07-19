# kmod runtime across releases: the jump is the v6.18 merge window (2026-07-19)

The kselftest `kmod` collection takes about 250 seconds on the current
setup, and memory said it was once much faster. A per-release sweep
(v6.0..v6.18, v7.0, v7.1, v7.2-rc3; driver at
`$SYSTEM_DIR/sweep/sweep-kmod.py`, results in
`$SYSTEM_DIR/sweep/kmod-releases.jsonl`) measured it on identical
hardware, guest shape, and measurement path (the `kselftest@kmod` unit's
own start-to-exit time): per tag, a bringup at that tag (kernel plus the
tag's own kmod kselftests, selftests closure, guest `kmodsweep` replaced
each time), then the collection with a 600-second timeout.

## Result

| tag | runtime (s) | | tag | runtime (s) |
| --- | --- | --- | --- | --- |
| v6.0 | boot hang | | v6.11 | 87.5 |
| v6.1 | boot hang | | v6.12 | 110.6 |
| v6.2 | boot hang | | v6.13 | 114.2 |
| v6.3 | 144.0 | | v6.14 | 109.2 |
| v6.4 | 140.3 | | v6.15 | 110.4 |
| v6.5 | 101.6 | | v6.16 | 109.9 |
| v6.6 | 98.7 | | v6.17 | 107.9 |
| v6.7 | 101.6 | | v6.18 | 249.9 |
| v6.8 | 102.8 | | v7.0 | 249.8 |
| v6.9 | 84.8 | | v7.1 | 250.7 |
| v6.10 | 89.1 | | v7.2-rc3 | 252.8 |

One clean step: **v6.17 runs in 107.9 s, v6.18 in 249.9 s**, and the
runtime is flat on both sides of it (85-115 s across v6.5..v6.17, 250 s
from v6.18 on). The regression, whether in the kernel's module loader or
in `kmod.sh` itself (both travel with the tree), landed in the v6.18
merge window. The recollection of "about 400 s" resolves to this 250 s
plateau; the only prior recorded measurement was 4m00s at v7.2-rc1
during the suite integration, consistent with the plateau.

## Toolchain and vintage dodges (why the sweep recipe looks odd)

- All tags build with clang: the devShell's GCC 15 defaults to C23,
  which breaks the x86 realmode boot code on every release tag before
  the upstream `-std=gnu11` pin; release tags never got the backport.
- `KCFLAGS=-Wno-enum-enum-conversion` everywhere KVM's `-Werror` meets
  clang 21 (v6.6 and v6.7 confirmed; codegen-neutral, so it is the
  default for tags built after it was learned).
- `RUSTC=rustc-unavailable` for v6.14..v6.18: those trees accept the
  modern `rustc` version-wise but emit old-style custom target specs it
  rejects ("custom targets are unstable"); pointing RUSTC at nothing
  fails the configure-time probe and Kconfig drops RUST cleanly.
- v6.0..v6.2 cannot boot at all under a `vhost-user-fs` device: an
  unhandled early-boot exception (vCPU halted in
  `early_fixup_exception`, interrupts off, before any console
  registers), independent of QEMU version, machine type, RAM, queue
  size, and KASLR; fixed somewhere in the v6.3 merge window. A separate
  finding with its own reproduction matrix; those three tags stay
  honest holes.

## Next: commit-level blame

The `f/kernel/bisect` flow's `selftests` payload hunts this directly:
bad `v6.18`, good `v6.17`, suites `[kmod]`, `max_runtime` 180 (between
the plateaus), per-test timeout 600, compiler clang plus the make flags
above so every candidate builds and the endpoints reuse the sweep's
published kernels. Because the test scripts travel with the tree, the
first bad commit may be a `kmod.sh` change rather than a kernel change;
either way it names what doubled the runtime. This note gets the commit
once the hunt lands it.
