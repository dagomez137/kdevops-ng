# KUnit on-demand execution findings (v7.2-rc1/rc3, 2026-07-19)

Two upstream-report candidates uncovered while validating the new
`f/kernel/bisect` flow with the suites that failed the full-catalog run on
guest `vanilla` (kernel `7.2.0-rc3-vanilla-82dc6ef41ea2`). Both were assumed
to be regressions; the bisect flow's endpoint verification proved neither
has a first-bad commit in reachable history. Both reproduce standalone at
v7.2-rc1 AND v7.2-rc3, and both are latent kernel bugs that our execution
model (tests as modules, `kunit.autorun=0`, suites triggered on demand
through `/sys/kernel/debug/kunit/<suite>/run`) is the first to exercise.

## Finding 1: `bitfields` (and `iov_iter`): debugfs run node on freed __init text

`lib/tests/bitfield_kunit.c` marks every test function `__init` (lines 60,
103, 129 at v7.2-rc1) but registers with plain `kunit_test_suites()` (line
151), not `kunit_test_init_section_suites()`. Only the latter sets
`suite->is_init`, and `lib/kunit/debugfs.c:206` withholds the debugfs `run`
node exactly when `is_init` is set. So `bitfields` exposes a `run` node it
must not have.

Built as a module with autorun off, the `__init` text is freed when
`modprobe` returns and the tests have never run; the first on-demand
trigger jumps into freed memory. Observed outcomes, all from one cause:

- vanilla full-catalog run (rc3): `Oops: int3` (poison bytes) in
  `kunit_try_catch`, RIP inside `af_packet`'s freed init area, escalating
  to `Kernel panic - not syncing: Fatal exception in interrupt`.
- bisect guest standalone (rc3): both cases `try faulted`, `internal error
  occurred preventing test case from running: -4`, guest survived
  (containment depends on where the stale pointer lands).
- bisect guest standalone (rc1): `Oops: 0000 (#PF, not-present page)`
  during `test_bitfields_constants`; guest wedged.

`lib/tests/kunit_iov_iter.c` has the same pairing (`static void __init`
test functions, `kunit_test_suites()` at line 1251), so `iov_iter` is the
same hazard; the catalog run panicked at `bitfields` first alphabetically.

Reproducer, CONFIRMED live on the bisect guest at rc1 (2026-07-19): after
`modprobe bitfield_kunit`, `ls /sys/kernel/debug/kunit/bitfields/` shows
the `run` node that `is_init` should have withheld, and triggering it
crashed the guest with a fourth flavor, a general protection fault on a
non-canonical address in `string+0x4a` (vsnprintf chasing a pointer out of
the freed suite data). Four triggers, four crash shapes, one cause:

    modprobe bitfield_kunit
    echo any > /sys/kernel/debug/kunit/bitfields/run   # oops/GPF/panic

Fix candidates: register via `kunit_test_init_section_suites()` (debugfs
then withholds `run`), or drop `__init` from the test functions so re-runs
are legal. Audit sweep upstream: any `kunit_test_suites()` user with
`__init` cases.

## Finding 2: `amdv1_iommu_test`: pt_kunit_dev registration collisions

Standalone at rc1 and rc3: 2/7 cases pass, then `test_map_table_to_oa`,
`test_unmap_split`, `test_random_map`, `test_pgsize_boundary`, `test_mixed`
all fail; in the ordered vanilla run each failing case pair (`amdv1_cfg_0`/
`amdv1_cfg_1`) logged `Error: Driver 'pt_kunit_dev' is already registered,
aborting...`. Every case calls `kunit_device_register(test, "pt_kunit_dev")`
(`drivers/iommu/generic_pt/kunit_iommu.h:115`); the previous case's
teardown has not finished releasing the same-named driver when the next
case registers it. Not ordering-dependent and not a regression: fails
identically standalone at both endpoints. `drivers/iommu/generic_pt` is
unchanged v7.2-rc1..rc3; the `amdv1_cfg_1` (32-bit VA) config the failures
implicate landed in the 7.2 merge window (`d9f759704b546`,
`5240dab55b51c`).

## What the bisect flow proved

Both suites were fed to `f/kernel/bisect` (bad=v7.2-rc3, good=v7.2-rc1).
Both runs concluded `good_endpoint_failed` in two iterations each, minutes
instead of hours: the endpoint verification phases did exactly their job,
refusing to bisect ranges whose good end is not good. A synthetic full
bisect (fake verdicts against the real Bare, culprit seeded at the
v7.2-rc2 commit) converged in 9 feed steps, so the machinery is ready for
a real range once a passing good endpoint exists for some future failure.

Operational notes from the runs: the suite name is `bitfields`, not the
module name `bitfield_kunit` (first run 2 attempt tested a nonexistent
suite; honest red). A literal-SHA `git_ref` gets the SHA as its build
identity label, so tag-built kernels are not reused for the same commit.
The `f/qsu/settle` step was added after the first live iteration hit the
boot-settling race (discover probing before sshd was up).
