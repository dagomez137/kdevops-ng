# Build identity is a content hash baked into kernelrelease

The Store is a cross-host registry, so an artifact's key must be a true content
identity. `kernelrelease` alone is not, because two builds of the same commit
with different configs (KASAN on/off, gcc vs clang) yield the same release string
but different binaries, and a peer fetching that key would boot the wrong kernel.
We therefore key the Store by a **Build identity** = a hash of the build inputs
(config, the Nix devShell's toolchain store hash, make flags, source commit), and
we **bake that hash into `kernelrelease` via `CONFIG_LOCALVERSION`** so the running
kernel self-reports its identity. The hash is computed over the config with the
`LOCALVERSION` line excluded, which breaks the otherwise circular dependency
(the hash feeds a config field that would feed the hash).

## Status

accepted

## Considered Options

- **Key by `kernelrelease` only**. Rejected: collides across configs; unsafe for
  a shared registry.
- **External hash (Store directory name only), vanilla `kernelrelease`**.
  Rejected: modules resolve at `/lib/modules/$(uname -r)/`, so every config of one
  version would collide in `/lib/modules/<release>/` inside the booted VM. The hash
  must be intrinsic to `kernelrelease`, not just the directory key.

## Consequences

- Image and modules share one identity automatically, so multiple VMs can boot an
  already-built identity without recompiling, and a fetched artifact is provably
  the one requested.
- Toolchain identity comes free from Nix (the devShell store hash); provenance is a
  small manifest (mostly Windmill's existing JSON output plus the shipped config):
  no bespoke metadata subsystem.
- Reproducibility makes fetch and rebuild interchangeable (same identity ⇒ same
  bytes): **empirically confirmed cross-host** for kernel and QEMU on two real
  machines (different OS and nix version), and the toolchain-identity premise holds
  (identical compiler store paths from one `flake.lock`). Evidence:
  `~/kernel/repro/` (HANDOFF.md, FINDINGS-R0.md, FINDINGS-QEMU.md); see ADR-0004.
- The **run layer is not independently reproducible**: `--build-id` is a SHA1 over
  the output *including debug sections*, embedded in `.rodata`, so any debug-path or
  producer difference cascades into `bzImage`. The path/producer fixes below are
  load-bearing for the run layer, not just the debug layer.
- Still to implement (consumer side): the kernel per-build map
  `-fdebug-prefix-map=<worktree>/=` in `KCFLAGS`+`KAFLAGS` (exactly one map, keyed on
  the **worktree** prefix, not the build dir) and the QEMU
  `-ffile-prefix-map=<source-root>=/qemu`; plus the Build-identity-hash injection via
  `LOCALVERSION` (T7), randstruct seed and module-signing. `KBUILD_BUILD_TIMESTAMP/
  USER/HOST` are already set; `setlocalversion`'s `+` is a latent (not blocking) hazard.
