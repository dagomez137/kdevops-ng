# Handoff: prepare the two KUnit upstream reports

For a fresh agent. The next session's mission: turn the two live-confirmed
KUnit findings into upstream reports (and likely patches), for the kernel
lists. The findings themselves are fully documented in the tree; this doc
carries the session context, evidence locations, and the open analysis
items the reports still need.

## Read first (reference, do not duplicate)

- `notes/kunit/ondemand-init-text-findings.md` (committed `1d6d184`): THE
  canonical findings write-up: both bugs, all four crash flavors, the
  confirmed two-line reproducer, source file:line refs at v7.2-rc1, fix
  candidates. Everything below assumes you have read it.
- Memory `kernel-bisect-flow` (project): how the findings were produced
  and the flow's gotchas (suite vs module names, SHA-label reuse).
- Memory `kunit-suite-executor` + `docs/flows/kunit.rst`: the on-demand
  execution model (autorun off, modules, debugfs trigger) that exposes
  both bugs; the report must explain this model since upstream autorun
  users never hit it.
- `docs/flows/bisect.rst` + `f/kernel/bisect*`: the tool that proved
  neither bug is a bisectable regression (both fail standalone at
  v7.2-rc1 AND v7.2-rc3).

## Report 1: freed __init text runnable via the KUnit debugfs run node

Claim (verified in source at v7.2-rc1, confirmed live): a suite whose test
functions are `__init` but which registers via plain `kunit_test_suites()`
never gets `suite->is_init` set, so `lib/kunit/debugfs.c:206` creates the
`run` node it exists to withhold. Built `=m` with `kunit.autorun=0`, the
init text is freed at modprobe return with the tests never run; the first
debugfs trigger executes freed memory. Affected: `lib/tests/
bitfield_kunit.c` (confirmed crashing, 4 flavors) and
`lib/tests/kunit_iov_iter.c` (same pairing, line 1251; NOT yet triggered
live; do one confirming run before reporting it as affected).

Still to do for the report:
- Extract the full crash traces from the host journals (this host,
  `hz-debian`; journal is durable):
  - int3 poison -> fatal panic: `journalctl --user -u
    qemu-system@vanilla.service --since "2026-07-18 22:33" --until
    "2026-07-18 22:35"` (kernel `7.2.0-rc3-vanilla-82dc6ef41ea2`).
  - contained `try faulted` (rc3) + `#PF` oops (rc1) + GPF in
    `string+0x4a` (rc1, the deliberate repro at Jul 19 00:49): same for
    `qemu-system@bisect.service` around `2026-07-18 23:5x`–`2026-07-19
    00:50`.
- Decide report vs patch. The user is a kernel developer and likely wants
  patches: candidate fix is switching both files to
  `kunit_test_init_section_suites()`. VERIFY FIRST what that macro does
  for MODULAR suites at v7.2 (it wraps suites as init-section: check
  `include/kunit/test.h` semantics; is_init suites get results-only
  debugfs, and check whether a modular init-section suite still autoruns
  on load with autorun=0 — i.e. does the fix make the suite results-only
  under our model, which is the correct honest behavior). Alternative fix:
  drop `__init` (keeps the suites re-runnable; costs resident text).
  Present both, recommend one.
- Sweep for other affected suites: grep the tree for files pairing
  `static void __init.*struct kunit` with `kunit_test_suites(` (only
  lib/tests/ was swept). A one-liner over the worktree
  (`$WORKERS_DIR/0001/main/linux` on this host, currently at rc1) or the
  Bare. Include the sweep result in the report.
- `scripts/get_maintainer.pl` in the worktree for recipients (expect the
  KUnit maintainers + linux-kselftest@ + kunit-dev@ + the two files'
  authors). Wrap at 78 cols, plain text, per kernel process.

## Report 2: amdv1_iommu_test pt_kunit_dev registration collisions

Claim (live-verified failure, mechanism NOT yet root-caused): 5/7 cases
fail standalone at rc1 and rc3, with `Error: Driver 'pt_kunit_dev' is
already registered` per case in the ordered run. Every case calls
`kunit_device_register(test, "pt_kunit_dev")`
(`drivers/iommu/generic_pt/kunit_iommu.h:115`).

Still to do before this one is sendable:
- Root-cause the collision: read `lib/kunit/device.c`
  (`kunit_device_register` -> driver lifetime; the driver is released via
  a test-managed action) and figure out why case N's registration survives
  into case N+1 (async kobject release? the suite's own init? the first
  two cases passing but later ones colliding is a clue: which two cases
  pass and what do they NOT do?). Re-run the suite alone with
  `stream_logs` and read the per-case KTAP: `wmill flow run f/kunit/run
  -d '{"vm_name":"<vm>","suites":["amdv1_iommu_test"],"service":{}}'` on
  a KUnit-ready guest (rebuild `bisect` via bringup, or use `vanilla`
  which is rc3 and running).
- Check lore for prior reports: the generic_pt kunit work is recent
  (7.2 merge window, `d9f759704b546`, `5240dab55b51c`); search
  lore.kernel.org for pt_kunit_dev / amdv1_iommu_test failures first.
- Recipients via get_maintainer.pl on `drivers/iommu/generic_pt/` (expect
  Jason Gunthorpe + iommu@lists.linux.dev + kunit lists).

## Repro environment facts (for either report's "how to reproduce")

- Kernel: torvalds tags v7.2-rc1 / v7.2-rc3, x86_64, gcc, imageless
  preset (`vendor/linux-config-fragments/defconfigs/imageless_defconfig` +
  `kernel/configs/test/kunit.config`): `CONFIG_KUNIT=y`,
  `CONFIG_KUNIT_DEBUGFS=y`, `CONFIG_KUNIT_ALL_TESTS=m`, autorun off.
  QEMU q35/kvm guest, NixOS closure. Nothing kdevops-specific is load
  bearing for the report: the reproducer is modprobe + one debugfs write
  (report 1) or triggering the suite via debugfs (report 2).
- The suite name is `bitfields`; the module is `bitfield_kunit`.
- Published kernels for both endpoints exist in the store
  (`kernel-7.2.0-rc{1,3}-<sha>-*`), so re-running any experiment is
  minutes: bringup a fresh guest with `test_suites: ["kunit"]` and
  `custom_ref` at the wanted SHA, or reuse `vanilla` (rc3, running).

## Environment state

- Guests: `vanilla` running (rc3, KUnit-ready, 112-suite cache);
  `bisect` STOPPED (last kernel rc1; crashed by the deliberate repro).
  Bringup replaces it cleanly (vm_target new, vm_name bisect).
- `main` is ~76 commits ahead of origin, push is a USER action. The
  bisect-flow work is `4161ec9..1d6d184`; workspace deployed + in sync.
- Bisect state dir `$SYSTEM_DIR/bisect/bisect/` still holds the last
  concluded run (good_endpoint_failed for bitfields); any rerun with new
  inputs resets it.
- xfstests watchdog live-fire and the Windmill fork GitHub push remain
  older pending items (see `handoff-notes-index` memory).

## Suggested skills

- `kernel`: MANDATORY for the reports/patches (loads
  technical-patterns.md; patch review protocol if patches are written).
- `cli-commands` for `wmill` (always `nix run .#wmill --`; push-only
  sync; flow run examples above).
- `nix` if the fix-validation loop needs closure/preset changes.
- lore.kernel.org is in the allowed network hosts for prior-art searches.

## Redaction

No secrets in this doc. Windmill/Grafana tokens live only in their secret
variables; nothing here needs them.
