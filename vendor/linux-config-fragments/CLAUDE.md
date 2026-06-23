<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->
# linux-config-fragments

Modular Linux kernel `.config` fragments for building custom kernels for
QEMU/KVM guests, composed with the kernel's own
`scripts/kconfig/merge_config.sh`. Tristate features default to `=m`; the
`builtin/` mirror provides `=y` overrides for module-free boots.

## Layout

- `kernel/configs/<group>/` — feature fragments grouped by topic (`core`,
  `arch`, `fs`, `storage`, `virt`, `net`, `security`, `mem`, `debug`, `test`,
  `rust`, `perf`).
- `kernel/configs/builtin/<group>/` — `=y` overrides mirroring the same paths.
- `defconfigs/` — whole-kernel configs (e.g. the imageless VM kernel).
- `scripts/verify_config.sh` — checks a merged `.config` (see Validation).
- `docs/` — design and verification docs.

## Conventions

- Fragments follow the upstream kernel `kernel/configs/` convention: a
  `# Help: <one line>` header, then `CONFIG_*` values. Tristate features are
  `=m`; the matching `builtin/` fragment sets them `=y` and inlines every
  tristate dependency so it is self-contained.
- Fragment file names are kebab-case (`vm-debug.config`,
  `xarray-no-multi.config`).
- **SPDX.** Follow the kernel's rule
  (`Documentation/process/license-rules.rst`): an `SPDX-License-Identifier:
  copyleft-next-0.3.1` goes on source code, scripts, and structured docs. Here
  that is `scripts/verify_config.sh` (line 2, after the shebang, `#` style) and
  the `.md` docs. The `.config` fragments and defconfigs are Kconfig data, not
  source: the kernel tags none of its own `kernel/configs/*.config` and
  `checkpatch.pl` only requires a tag on `.c/.h/.s/.S/.rs/.dts/.dtsi`, scripts
  and `.rst`, so the fragments carry none here and are covered by `COPYING`. Do
  not add SPDX to `.config` files.

## Validation

Run `scripts/verify_config.sh` on an affected merge after any fragment change,
to confirm the values you requested survive into the final `.config`. See
[docs/verification.md](docs/verification.md). The design rationale — why `=m`
is the default, the `builtin/` override model and `merge_config.sh -y`, the
Kconfig-forced symbols, and the deliberate omissions — is in
[docs/design-decisions.md](docs/design-decisions.md).

## Commit rules

All commits must follow these rules.

1. One commit per change. Atomic commits only; do not mix unrelated changes.
2. Write the subject as `subsystem: summary` in the imperative mood, within 75
   characters. The subsystem is the area changed — a group name (`core`, `fs`,
   `storage`, `virt`, `net`, `security`, `mem`, `arch`, `perf`, `rust`,
   `debug`, `test`) or `defconfigs`, `scripts`, `docs`, `fragments`.
3. Wrap the commit body at 75 columns.
4. Sign off with the git-configured identity (`git commit -s`, a
   `Signed-off-by` trailer). This certifies the [DCO](DCO).
5. Mark AI-generated work with a `Generated-by: Claude AI` trailer placed
   immediately before `Signed-off-by`, with no blank line between them.
6. Run `scripts/verify_config.sh` on an affected merge before committing a
   fragment change.
