# XArray and maple tree across releases: no regression, one flaky race (2026-07-19)

A per-release sweep (v6.0..v6.18, v7.0, v7.1, v7.2-rc3; driver at
`$SYSTEM_DIR/sweep/sweep-xarray-maple.py`, results in
`$SYSTEM_DIR/sweep/xarray-maple-releases.jsonl`) measured the XArray and
maple tree test families in both execution forms on one booted guest per
tag: the in-kernel module-init tests (`test_xarray`, `test_maple_tree`
via the runtime-tests suite) and the userspace harness binaries
(`radix-tree/xarray`, `radix-tree/maple` via the usertests suite, built
from each tag's own `lib/` sources). Every item ran with the suites' new
`repeats` knob at 3; the reports fold repeats into median runtime,
min/max spread, per-run list, tests-per-second throughput, and a
count-variance flag, and the evaluation below judges those statistics,
never a single sample.

## Verdict: nothing to bisect

Across v6.3..v7.2-rc3 there is no runtime or throughput regression in
either family, in either form. The apparent single-sample "jumps" of the
first pass all dissolved under counts and repeats:

- v6.9 xarray runtime rose ~2x in both forms because the test content
  grew 4.8x (the advanced multi-index tests, `a60cc288a1a26` and
  siblings); throughput IMPROVED (in-kernel 35.8 -> 89.9 M/s, userspace
  15.7 -> 21.1 M/s).
- The maple tree runs 3.4x..23x FASTER from v6.18 on: the mm-stable pull
  carried the maple_tree sheaf conversion (`59faa4da7cd45` "use percpu
  sheaves for maple_node_cache", the prefilled-sheaf and `kfree_rcu`
  conversions). This is the same slab sheaves series whose
  `kfree_rcu()` batching commit (`ec66e0d599520`) slowed the kselftest
  kmod collection 2.3x (notes/selftests/kmod-runtime-sweep.md): one
  series, a cost on module churn, a large win on allocation-heavy maple
  workloads; both sides now measured.
- The v6.12 maple-module +13% (identical counts) never compounded and
  stayed below the 30% materiality bar; a watch item only.
- The maple test counts are nondeterministic run to run in BOTH forms
  (randomized content; the count-variance flag fires consistently), so
  maple comparisons must use throughput and medians, never raw counts.
  The in-kernel count-semantics also changed at v6.5 (the "make test
  code work without debug enabled" rework), splitting that series into
  incomparable halves.

## The finding: a flaky RCU slow-read assertion in the maple harness

At v7.0, one of `radix-tree/maple`'s three runs aborted (exit 6) after
492,289,902 of 492,289,903 asserts:

    maple.c:34667: run_check_rcu_slowread(...): Assertion `0' failed.

An RCU slow reader observed an inconsistent value: an intermittent race
at roughly 1-in-3 per run on that tag, caught only because the repeats
ran the harness three times. The clean 3/3 records at v6.18, v7.1 and
v7.2-rc3 do not exonerate those tags (3/3 passes by chance ~30% of the
time at that rate), so the flake is NOT localized and may predate the
sheaf conversion; suspicious company is noted, not proven.

Next step before any bisect: qualify the rate. Run the usertests flow
with `harnesses: [radix-tree/maple]`, `repeats: 15` on v7.2-rc3 first
(if it reproduces on the newest kernel it is a live upstream bug worth
reporting regardless of origin), then on v6.18 and v6.17 to bracket. A
bisect over a flaky verdict is sound once the rate is known: the bisect
flow's `service.repeats` uses worst-of-N semantics, so at a ~1/3 rate,
`repeats: 10` reads a truly affected candidate as bad with ~98%
confidence while unaffected candidates always read good.

## Sweep mechanics worth keeping

The kernels were store hits from the kmod sweep (same per-tag clang
recipe); each tag cost only the harness build, closure, boot and runs.
Vintage holes recorded honestly: v6.0..v6.2 are the known
vhost-user-fs early-boot hang; v6.3's harness does not compile under
clang (`lib/maple_tree.c` uses `fallthrough`, which the vintage
tools/include shims define only for GCC), so the driver's fallback
reruns bringup without the harness and keeps the module-form
measurement.
