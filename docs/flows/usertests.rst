.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

===================================
Run kernel userspace test harnesses
===================================

The :src:`f/usertests/run` flow runs the kernel's userspace test
harnesses on a booted guest: the plain userspace binaries under the
kernel tree's `tools/testing`_ (``radix-tree``, ``vma``, ``rbtree``,
``memblock``, ``scatterlist``), which compile kernel sources
(``lib/xarray.c``, ``lib/maple_tree.c``, ``mm/vma.c``, ``mm/memblock.c``,
``lib/rbtree.c``, ``lib/scatterlist.c``) straight into userspace through
the ``tools/testing/shared`` shim. The coupling is therefore inverted
relative to every other suite: **the binaries test the source tree they
were built from, not the booted kernel**; the guest only hosts the run.
The artifact stays keyed by the build's kernel release, so the verdict
always names the source it covers.

Uniquely, this suite has **no kernel-configuration surface at all**: the
harnesses need no ``.config``, no ``make headers``, and no fragment; the
build needs only a toolchain and liburcu (the shim maps kernel RCU onto
userspace RCU). The build side lives in :src:`f/kernel/build`'s
**Usertests** group, which builds the selected harness directories
(default ``radix-tree``, ``vma``, ``memblock``; ``scatterlist`` is
selectable but off the default, see below) in the ``build-usertests``
devShell, stages the expected binaries, cleans the in-tree litter, and
publishes the tree to the Nix store as ``usertests-<release>``. The
guest-side unit,
``usertests@<instance>.service``, comes from the vendored module
(:src:`vendor/nixos-flake/modules/testSuites/usertests.nix`).

The flow is thin:

1. ``discover``: gate the guest, resolve the ``usertests-<release>``
   artifact matching the booted kernel, enumerate the staged binaries
   from its ``MANIFEST``, refuse an empty enumeration, cache for the
   picker.
2. ``prepare``: copy the store tree onto the writable ``usertests``
   share, keyed by kernel release.
3. ``render_config``: write one env file per binary
   (``env/<dir>/<binary>.env``, the ``$ARGS`` each unit instance reads),
   carrying the **Seed** and **Long Run** knobs to ``radix-tree/main``
   and ``--verbose`` to ``memblock``.
4. for each ``harness`` in turn: ``start`` → ``wait`` → ``collect``.
5. ``report`` → ``judge``, with the ``stop`` failure module.

Verdict policy per binary
=========================

The harnesses were built with AddressSanitizer and UBSan hardwired, and
their failure conventions differ, so the curated catalog encodes each
one (all source-verified):

- **Exit codes are real** for the whole family: failures abort
  (``assert``/SIGABRT), and the unit has no ``-`` prefix, so a failed
  start job with a populated exit status is an honest test failure.
  ``vma/vma`` additionally returns pass/fail properly and prints
  ``N tests run, N passed, 0 failed.``
- **Summary counts** guard truncation where they exist:
  ``XArray: N of N tests passed``, ``maple_tree: ...``, ``IDA: ...``;
  ``radix-tree/main`` ends with the ``tests completed`` sentinel and
  logs ``random seed N`` for reproduction.
- **The rbtree pair never fails by exit code**: ``rbtree_test`` and
  ``interval_tree_test`` report broken invariants only as
  ``assertion failed at <file>:<line>`` lines on stderr and keep going,
  so their verdict is a clean exit **and** zero such lines; their
  cycle-count output is benchmark data, not verdict. The pair is
  **currently excluded from the catalog**: at v7.2-rc1 their kernel
  sources call the new ``kmalloc_objs()`` helper, which the
  ``tools/testing/shared`` shim does not provide, so the harness does
  not compile (an upstream fix candidate); the assertion-line rule
  above stays encoded for their return.
- ``scatterlist`` is **off the default build set**: the harness has
  been unbuildable upstream since v6.2 through three stacked shim
  gaps (``zone_device_pages_have_same_pgmap()``, ``struct folio``,
  ``page_range_contiguous()`` plus direct ``struct page`` arithmetic),
  each pinned to its first bad commit with :doc:`the bisect flow's
  <bisect>` ``usertests_build`` payload. All three are upstream fix
  candidates; pick the harness back in only for a tree where it
  builds.
- ``radix-tree/idr-test`` (and ``main``) print an **expected** noise
  block bracketed by ``vvv Ignore "not allocated" warnings`` and
  ``^^^``; assertion lines inside it are part of the test and are
  whitelisted by the scanner.
- Sanitizer findings gate every binary: any
  ``ERROR: AddressSanitizer``, ``ERROR: LeakSanitizer`` or UBSan
  ``runtime error:`` line fails the item. The policy is pinned in the
  unit (ASan aborts, UBSan halts, LeakSanitizer gates), not left to
  library defaults.

The run form
============

**Harnesses** picks the binaries to run (curated labels, benchmarks
last); empty runs everything the artifact stages. **Seed** seeds the
randomized ``radix-tree/main`` (0 keeps it random; the printed seed is
archived in the report either way). **Long Run** passes ``-l`` to
``radix-tree/main`` (its extended mode runs for tens of minutes).
**Repeats** (default 1) runs each picked harness that many times back to
back; the report folds same-item runs into one entry whose ``time(s)`` is
the median, with the min/max spread, the per-run list, and a ``tests/s``
throughput beside it, so a runtime comparison rests on a statistic
instead of one boot's noise (a single failing run still fails the job).
The **Service** group bounds the run: **Item Timeout** defaults to 1200
seconds because ``radix-tree/main`` carries a hard 30-second sleep floor
and the sanitized ``maple``/``xarray`` runs take minutes.

Watching a run
==============

The job log streams each harness's output live (``stdbuf`` line-buffers
it; the journal socket would otherwise hold everything until exit and
lose a crashing test's context). ``report`` renders the usual sortable
tables with the ``time(s)`` column, and the rollup lands host-side:

.. code-block:: console

   $ cat "$WORKERS_DIR/shared/usertests/<vm>/<kver>/report.json"

Running a harness from the CLI
==============================

An item is ``<dir>/<binary>``, so the unit instance is its
:cmd:`systemd-escape` form:

.. code-block:: console

   $ systemctl --host <vm> start --no-block \
       usertests@"$(systemd-escape 'radix-tree/main')".service
   $ ssh <vm> journalctl --unit='usertests@*' --output=cat --follow

Re-runs need no cleanup: the binaries hold no persistent state on the
guest (all state is heap), and repeated starts simply run them again.
To reproduce a randomized ``radix-tree/main`` run, put its logged seed
in the env file the unit reads
(``/var/lib/usertests/<kver>/env/radix-tree/main.env``,
``ARGS=-s <seed>``), or set the form's **Seed** and re-run the flow.

Stopping a run
==============

Cancelling the Windmill job runs the ``stop`` failure module
(:src:`f/usertests/stop.py`), which stops and resets the run's
instances idempotently. ``wait`` owns the deadline
(:cmd:`TimeoutStartSec` stays ``infinity``), and the host
``qemu-system@<vm>.service`` liveness check turns a guest death into a
failed item, never a false pass.

.. _tools/testing: https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/tools/testing
