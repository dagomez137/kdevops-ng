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

### Qualification (2026-07-19/20): long-latent, not bisectable

The rate was qualified with the flows' `repeats` knob, plus a host
control:

| leg | runs | failures |
| --- | --- | --- |
| v7.2-rc3 guest | 18 | 1 |
| v7.1 guest | 3 | 0 |
| v7.0 guest | 3 | 1 |
| v6.18 guest | 33 | 1 |
| v6.17 guest | 48 | 1 |
| v7.2-rc3 host (same binary) | 15 | 0 |

Pooled: 4 failures in 105 guest runs, about 4% per run, statistically
uniform across every tested tag; v6.17 failing kills the sheaf-era
hypothesis, so the race is long-latent (present at least since v6.17,
plausibly far older) and NOT a regression with a reachable good
endpoint, hence not bisectable. It is also environment-sensitive: the
identical rc3 binary stayed clean in 15 host runs, so the 4-vCPU guest's
timing is part of the reproduction. The assertion signature was captured
live at v7.0 (`maple.c:34667`) and v7.2-rc3 (`maple.c:34697`), the same
`run_check_rcu_slowread` assert both times, aborting within the last few
asserts of a ~490M-assert run.

Upstream report candidate, reproducer included: build
`tools/testing/radix-tree` at any recent tag, run `./maple` repeatedly
in a small VM (4 vCPUs); expect the RCU slow-read assertion within a few
tens of runs. Whether the inconsistency is in `lib/maple_tree.c`'s RCU
semantics or in the harness's userspace-RCU modelling of them is the
question for the maple maintainers; the failing window (a reader
observing a stale range during store) is genuine either way.

The qualification also exposed and fixed a reporting gap: the repeats
fold kept only the last run's detail, discarding a mid-sequence
failure's assertion tail; the fold now takes counts from the last
passing run and evidence from the last failing one (`c78b855`).

## Sweep mechanics worth keeping

The kernels were store hits from the kmod sweep (same per-tag clang
recipe); each tag cost only the harness build, closure, boot and runs.
Vintage holes recorded honestly: v6.0..v6.2 are the known
vhost-user-fs early-boot hang; v6.3's harness does not compile under
clang (`lib/maple_tree.c` uses `fallthrough`, which the vintage
tools/include shims define only for GCC), so the driver's fallback
reruns bringup without the harness and keeps the module-form
measurement.
