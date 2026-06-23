# Kernel Rust toolchain requirements (rustc / bindgen / rustfmt)

Building a kernel with `CONFIG_RUST=y` needs a Rust toolchain inside the kernel's
*supported version window*, and that window moves with the kernel version. This doc
records **where the requirements are stated**, **how to cross-match a kernel to a
toolchain**, and **what our nixos-flake `#build-kernel` shell provides**, so a "bump
the kernel" or "bump the toolchain" task does not have to re-derive any of it.

Status: **`#build-kernel` ships nixpkgs rustc (1.95 on nixos-26.05), which suits
modern kernels (>= v7.1).** Older kernels (< ~v7.1) need an older pinned rustc — see
[Pinning an older toolchain](#pinning-an-older-toolchain).

## Where the requirements are stated (in priority order)

Check these *for the exact kernel ref you intend to build* — they differ between
versions. All paths are in the kernel source tree.

| Source | What it gives | Authority |
|---|---|---|
| `scripts/min-tool-version.sh` | The enforced **minimum** `rustc`, `bindgen`, `llvm` (and gcc) versions — `echo`'d per tool | **Canonical / machine-readable.** This is what the build actually checks. |
| `scripts/rust_is_available.sh` | The gate: reads `min-tool-version.sh`, probes the toolchain, exits non-zero (`*** Rust compiler '…' is too old`) if below min, which makes Kconfig set `RUST_IS_AVAILABLE=n` | The enforcement mechanism behind `make rustavailable`. |
| `Documentation/process/changes.rst` | Human "Minimal requirements" table (Rust, bindgen, GNU C, Clang/LLVM) | Human-readable mirror of the minimums. |
| `Documentation/rust/quick-start.rst` | Install instructions + the recommended toolchain and `RUST_LIB_SRC` wiring | Setup guide. |
| https://rust-for-linux.com/rust-version-policy | The **policy**: minimum tracks **Debian Stable's** rustc, advancing ~per Debian release; **no hard maximum** — new releases are CI-tested and have "worked with every version since the minimum" | The why behind the moving window. |

`bindgen` is CI-tested with no separate written policy; `rustfmt` is **not** version-gated
(it is optional formatting and effectively tracks the rustc version).

### Reading it for a given ref

```bash
# in a kernel checkout, for the ref you want to build:
scripts/min-tool-version.sh rustc      # -> e.g. 1.85.0
scripts/min-tool-version.sh bindgen    # -> e.g. 0.71.1
scripts/min-tool-version.sh llvm       # -> e.g. 15.0.0
# or read it straight out of the mirror without checking out:
git -C "$SYSTEM_DIR/mirror/linux.git" show <ref>:scripts/min-tool-version.sh | grep -A2 'rustc)\|bindgen)'
```

That minimum, plus "no hard max but newer is CI-tested", is the whole requirement.

## Cross-match table

Minimums are from `scripts/min-tool-version.sh` at each ref. "Custom-target gate" is
whether the core Rust build path passes `-Zunstable-options` (needed for **rustc >=
1.85** to load the kernel's custom target JSON; older rustc loaded it under
`RUSTC_BOOTSTRAP=1` alone). This is the single fact that decides whether a *new* rustc
works.

| Kernel | min rustc | min bindgen | core passes `-Zunstable-options`? | works with nixpkgs rustc 1.95? |
|---|---|---|---|---|
| v6.11 – v6.18 | 1.78.0 | 0.65.1 | **no** (relies on `RUSTC_BOOTSTRAP=1`) | **no** — 1.95 errors `custom targets are unstable` then `E0310` |
| v7.1+ | 1.85.0 | 0.71.1 | **yes** — added by `0a9be83e57de` ("pass `-Zunstable-options` for Rust 1.95.0") | **yes** |

Consequences:
- **No single rustc serves both ends.** v6.18 needs rustc in roughly **[1.78, 1.84]**
  (before rustc tightened custom-target loading); v7.1+ needs **>= 1.85** and tolerates
  1.95. A toolchain good for one silently fails the other.
- **The minimum bump is the trigger.** rustc min went 1.78 → 1.85 at v7.1 (Debian Stable
  cadence). bindgen min went 0.65.1 → 0.71.1. Always re-read `min-tool-version.sh` at the
  new ref before bumping the kernel.

To extend the table for a new ref, read its `min-tool-version.sh` (rustc + bindgen) and
check `rust/Makefile` / `scripts/Makefile.build` `cmd_rustc_library` for
`-Zunstable-options` on the core path.

## Footgun: a too-old toolchain builds *without* Rust, silently

If `rustc` is below the kernel's minimum, `rust_is_available.sh` fails, Kconfig sets
`RUST_IS_AVAILABLE=n`, and any preset with `CONFIG_RUST=y` is **silently downgraded** —
`alldefconfig`/`olddefconfig` drop it and the build **succeeds without Rust**. Confirm
Rust actually built by checking the resulting config:

```bash
grep -E '^CONFIG_RUST(_IS_AVAILABLE)?=' <build_dir>/.config   # want CONFIG_RUST=y
```

(See the planned "warn when `make rustavailable` fails" step in the project's
future-tasks list, which surfaces this instead of letting it pass quietly.)

## What `#build-kernel` provides today

The nixos-flake `#build-kernel` devShell (`lib/toolchain.nix` `matrixExtras`) ships
**nixpkgs' own** `rustc`, `rust-bindgen`, `rustfmt`, plus `rustPlatform.rustLibSrc` as
`RUST_LIB_SRC` (the kernel builds `core`/`alloc` from source). On nixos-26.05 that is
**rustc 1.95.0 / bindgen 0.72.1** — within the v7.1+ window.

## Pinning an older toolchain

To build a pre-v7.1 kernel with Rust, pin an older rustc (and matching rust-src, and a
compatible bindgen) into `#build-kernel`. The proven approach is a `rust-overlay` input:

```nix
# flake.nix
inputs.rust-overlay.url = "github:oxalica/rust-overlay";
inputs.rust-overlay.inputs.nixpkgs.follows = "nixpkgs";
# apply rust-overlay.overlays.default in the pkgs import, then:
# lib/toolchain.nix
rustForKernel = pkgs.rust-bin.stable."1.80.0".default.override {
  extensions = [ "rust-src" ];
};
# use rustForKernel in matrixExtras; RUST_LIB_SRC =
#   "${rustForKernel}/lib/rustlib/src/rust/library";
```

This was in place briefly for kernel 6.18 (nixos-flake history around the rustc-1.80
pin) before the project moved to modern kernels. The planned "configurable per-kernel
Rust requirements" task (future-tasks) is about selecting this automatically from the
kernel version range rather than editing the flake by hand.
