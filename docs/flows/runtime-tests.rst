.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

========================
Run kernel runtime tests
========================

The :src:`f/runtime_tests/run` flow runs the kernel's standalone runtime
test modules on an already-booted guest: the entries of
``lib/Kconfig.debug``'s `Runtime Testing`_ menu that are covered by neither
kselftest (:doc:`selftests`) nor KUnit (:doc:`kunit`). A run item is a
kernel **module**: loading it runs its whole suite in ``module_init``, so
the executor is upstream systemd's ``modprobe@<module>.service`` template,
already present on every guest, whose start job synchronizes on the
module's initialization. There is no suite userland and no share: the
kernel fragment (``test/runtime-tests.config`` in
:src:`vendor/linux-config-fragments`, carried by the imageless preset)
builds the modules, and any systemd guest whose booted kernel ships them
is ready.

The flow is thin and mirrors modprobe/systemd vocabulary one-to-one:

1. ``discover``: gate the guest over vsock-SSH (the ``modprobe@.service``
   template present), and derive which catalog modules the booted kernel
   ships from its ``modules.dep``. Zero present modules fails here.
2. for each ``module`` in turn: ``start`` → ``wait`` → ``collect``.
3. ``report``: fold the per-module results into one verdict.
4. ``judge``: fail the job unless every module passed.

What makes this family its own suite is that **exit conventions are per
module and deliberately inconsistent upstream**, so the curated catalog
encodes each one, verified against the sources:

- The **exit-honest** class (``test_xarray``, ``test_maple_tree``,
  ``test_rhashtable``, ``test_hexdump``, ``test_bpf``) returns 0 on pass,
  an error on failure, and stays loaded after a pass.
- The **auto-unload** class (``rbtree_test``, ``interval_tree_test``,
  ``percpu_test``, ``test_workqueue``, ``test_vmalloc``) always returns
  ``-EAGAIN`` so the module never stays loaded; real failures surface only
  as kernel ``WARNING``/``BUG`` lines, which ``collect`` scans for in the
  run-scoped kernel journal.
- ``test_ida`` **inverts** the convention: ``-EINVAL`` on pass, 0 on
  failure (and a passing run fires deliberate ``ida_free`` warnings, so
  the splat scan is off for it and the verdict rides its pass counts).
- ``atomic64_test`` BUGs the guest on failure, so its verdict is a clean
  load with the guest still alive.
- ``find_bit_benchmark`` is a benchmark: timings, no pass counts.

Two properties of the upstream unit shape the plumbing. Its ``ExecStart``
carries systemd's ``-`` prefix, so the start job finishes ``done``
whatever the module returned, and because systemd logs an expected
process exit only at debug level, the exit status never reaches the
journal at all: the verdict instead rides what is observable, the run
evidence in the kernel log (the summary counts or a per-module sentinel
line) and the module's post-run **load state**, which each class
determines (a passing exit-honest module stays loaded, a completed
auto-unload module is gone, and a still-loaded ``test_ida`` is precisely
a failed one). And the unit's ``ConditionKernelModuleLoaded=!%i`` skips
it entirely when the module is already loaded, which the flow treats as
a failed run identity (nothing ran); ``start`` therefore unloads a
stay-loaded module before starting, so every run is a fresh run.

Before starting the unit, ``start`` captures the guest journal's
end-of-now cursor: the job outcome, the process exit status, and the
kernel messages ``collect`` judges all come from after it, so a re-run can
never report a previous run's results. Every step carries a worker tag:
quick lifecycle steps on ``vm``, the ``wait`` poll on ``vm-run``. See
:doc:`../deployment/nix`.

The run form
============

**Modules** picks the modules to run from the booted kernel's catalog
(cached per VM by ``discover``; curated labels, benchmarks last); empty
runs every present catalog module. **Repeats** (default 1) runs each
picked module that many times back to back; the report folds same-item
runs into one entry whose ``time(s)`` is the median, with the min/max
spread, the per-run list, and a ``tests/s`` throughput beside it, so a
runtime comparison rests on a statistic instead of one boot's noise (a
single failing run still fails the job). The **Service** group bounds the
run:
**Item Timeout** (default 600 seconds) is ``wait``'s per-module deadline,
**Poll Interval** (default 10 seconds) paces the polls, and **Stream
Logs** (default on) prints the guest's merged unit and kernel journal into
the job log live, so the suites' own summary lines are visible as they
print.

Watching a run
==============

The job log is the primary live view (:doc:`guests`): the kernel journal
streamed by ``wait`` carries each suite's own summary (``XArray: N of N
tests passed``, ``test_bpf: Summary: N PASSED, 0 FAILED``, ...). When the
run finishes, ``report`` renders the three sortable tables and writes the
kver-keyed rollup:

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ cat "$WORKERS_DIR/shared/runtime-tests/<vm>/<kver>/report.json"

Running a module from the CLI
=============================

The flow's ``start`` step is one ``systemctl start`` of the upstream
template; the same command runs a suite by hand, and the kernel journal
carries its summary:

.. code-block:: console
   :class: cmd-host

   $ systemctl --host <vm> start modprobe@test_xarray.service
   $ ssh <vm> journalctl --dmesg --output=cat | grep "tests passed"

Mind the two idioms the flow automates for you: an exit-honest module
stays loaded after a pass, so a re-run needs
``modprobe --remove test_xarray`` first (an already-loaded module makes
the unit skip without running anything), and an auto-unload module's
``modprobe`` "failure" is its normal completion, with any real failure
sitting in the kernel log as a ``WARNING``. ``systemctl reset-failed``
clears a latched instance either way.

Stopping a run
==============

Cancelling the Windmill job runs the flow's ``failure_module``, which
stops the in-flight ``modprobe@`` instance, resets it, and unloads the
run's modules, idempotently (the documented fallback in
:src:`f/runtime_tests/stop.py`). A module whose test BUGs the guest takes
the VM down; ``wait`` detects that through the host
``qemu-system@<vm>.service`` on each poll and ``collect`` folds it into a
failed module rather than a false pass.

.. _Runtime Testing: https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/lib/Kconfig.debug
