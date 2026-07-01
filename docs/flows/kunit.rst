.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

================
Run KUnit suites
================

The :src:`f/kunit/run` flow runs `KUnit`_ test suites on an already-booted,
KUnit-ready guest: the Windmill equivalent of re-running a suite from the
kernel's debugfs interface, and the one commander of when suites execute.
The guest is produced separately by :src:`f/qsu/bringup` from a kernel built
with ``CONFIG_KUNIT=y`` and ``CONFIG_KUNIT_DEBUGFS=y`` (the imageless preset
and the ``test/kunit.config`` fragment both enable these, build every test
the enabled subsystems allow as a module via ``CONFIG_KUNIT_ALL_TESTS=m``,
and leave boot-time autorun off, so nothing executes until this flow asks),
booted with the ``kunit`` test suite so the closure ships the
``kunit@.service`` executor (all of the guest-side units come from one
vendored module, :src:`vendor/nixos-flake/modules/testSuites/kunit.nix`).
KUnit tests live in the kernel and its modules, so the flow needs no
userland and no guest share: results are read back over the SSH transport,
and the folded verdict lands host-side as ``report.json`` (below).

The flow is thin and mirrors KUnit/systemd vocabulary one-to-one:

1. ``discover``: gate the guest over vsock-SSH, confirm the ``kunit@.service``
   template and ``/sys/kernel/debug/kunit`` are present, and enumerate the
   suites the guest exposes (and which carry a ``run`` node). A guest
   exposing zero suites fails here rather than passing an empty run.
2. for each ``suite`` in turn: ``start`` → ``wait`` → ``collect``.
3. ``report``: fold the per-suite results into one verdict.
4. ``judge``: fail the job unless every suite passed, so a red run is a red
   Windmill job.

On the guest each suite runs as a template unit started with ``--no-block``:
``kunit@<suite>.service`` for a re-runnable suite, ``kunit-results@<suite>``
for an init-only suite (no ``run`` node; only its boot-time results can be
read). The re-run unit is two plain commands, no wrapper: it writes to
``/sys/kernel/debug/kunit/<suite>/run`` to re-run the suite, then
:cmd:`cat`\ s ``/sys/kernel/debug/kunit/<suite>/results`` (the `KTAP`_
document) to the journal. The write is strict: a failed trigger fails the
unit rather than collecting a stale ``results`` as if it were a fresh run.
The pass/fail verdict is the KTAP itself: ``collect`` fails the suite on a
``not ok`` line, and checks the suite's ``1..N`` plan against the parsed
result lines, so a journal truncated mid-suite can never read as a pass.

Before starting the unit, ``start`` captures the guest journal's end-of-now
cursor: everything after it belongs to this run and nothing before it does,
however fast the sub-second oneshot finishes. ``wait`` reads the run's
outcome from that same stream (systemd's own "Finished"/"Failed to start"
records), and ``collect`` parses only the run-scoped KTAP ``wait`` captured,
so a re-run can never report a previous run's results.

The unit sets :cmd:`TimeoutStartSec` to ``infinity``, exactly as the xfstests
executor does, so a hung suite is bounded not by systemd but by the ``wait``
step, which stops the unit on its own configurable timeout. Suites run one at
a time by design, and the kernel enforces the same: a ``run`` write blocks
until any other debugfs-triggered suite has finished (`Run tests without
kunit_tool`_). A KUnit-carrying kernel is a test kernel; the same page notes
KUnit is not designed for production systems, which is exactly why the guest
is a disposable VM.

Every step carries a worker tag: the quick lifecycle and control steps run on
the ``vm`` tag, and the ``wait`` poll runs on the ``vm-run`` tag, so a hung or
oopsing suite never starves a quick control op. See :doc:`../deployment/nix`.

The run form
============

The UI is the first-class way to run suites; the CLI sections further down
are the same machinery driven by hand.

**Suites** picks the suites to run, by name, from the guest's
``/sys/kernel/debug/kunit`` entries; empty runs every re-runnable suite the
guest exposes (an init-only suite has no ``run`` node, so it runs only when
picked explicitly). A form dropdown cannot reach the guest, so the picker
reads the per-VM cache the flow's ``discover`` step writes on each run:
after booting a different kernel the list is stale until the next run's
``discover`` refreshes it, and before the first discovery it falls back to
the curated set (``rust_rxarray``, ``rust_doctests_kernel``).

The **Service** group bounds the run. **Suite Timeout** (default 600
seconds) is ``wait``'s per-suite deadline; on expiry the unit is stopped.
**Poll Interval** (default 5 seconds) paces the status polls and journal
drains. **Stream Logs** (default on) prints the guest's merged unit and
kernel journal into the job log on each poll, so KTAP and dmesg are visible
live; switched off, the guest journal does not reach the job log at all, and
the CLI recipes below are how you read it afterwards.

Watching a run
==============

The job log is the primary live view (:doc:`guests` explains why): open the
running ``wait`` step and the guest's KTAP and kernel messages stream in as
each suite runs. When the run finishes, the ``report`` step renders three
sortable tables (run info, one row per suite, one row per test with failures
first), and the ``judge`` step turns the verdict into the job state, so a
schedule or an embedding flow sees a failing run as a failing job without
opening any table. The same rollup lands on the host as ``report.json``,
keyed by the guest's kernel release, so the verdict is recoverable from the
share alone:

.. code-block:: console

   $ cat "$WORKERS_DIR/shared/kunit/<vm>/<kver>/report.json"

Discovery and registration
==========================

A suite is runnable when it is *registered*: it then owns a directory under
``/sys/kernel/debug/kunit``. Registration and execution are decoupled, and
that split is what the whole flow rides on:

- A suite compiled into the kernel (``=y``, such as ``rust_rxarray``)
  registers during kernel init, so its debugfs directory exists as soon as
  the guest is up.
- A suite built as a module (the ``CONFIG_KUNIT_ALL_TESTS=m`` catalog)
  registers only when its module loads. The closure autoloads them all at
  boot: our ``kunit-test-modules.service`` (shipped by the same vendored
  module above, modeled on systemd's :cmd:`kmod-static-nodes` service) scans
  the booted kernel's ``/lib/modules`` for modules carrying the
  ``.kunit_test_suites`` ELF section and declares every match in
  ``/run/modules-load.d/kunit.conf``, the standard runtime drop-in directory
  of :cmd:`modules-load.d`, which upstream's :cmd:`systemd-modules-load`
  service then reads and loads from. No static module list could do this:
  which modules exist is a property of the booted kernel, and the scan is
  exact for whatever kernel booted.
- Autoloading is safe because registration does not execute anything: with
  autorun off (the default), a loaded suite just sits in debugfs waiting for
  its ``run`` node to be written. Booting costs the module loads, not the
  test time.

The UI queries this through the ``discover`` step: it lists the debugfs
directory over vsock-SSH (the ground truth), notes which suites carry a
``run`` node, and writes the names to a per-VM cache on the shared dir,
which is what the run form's **Suites** picker reads (a form dropdown cannot
reach the guest). Extra module names beyond the scan can be declared in the
guest configuration's ``nixos-flake.kunit.modules``; the
``builtin/test/kunit.config`` fragment mirror builds the whole catalog into
the kernel (``=y``) for a module-free guest.

Boot-time autorun
=================

By default nothing runs at boot; the flow decides when suites execute. To
run every registered suite once at boot instead, pick the curated
``kunit.autorun=1`` toggle in the **Kernel Parameters** field of the
**Kernel boot** group when booting the guest (:src:`f/qsu/boot` and
:src:`f/qsu/bringup` both offer it); it is appended to the kernel command
line for that VM. The build-time equivalents are the
``test/kunit-autorun.config`` fragment (flips the config default) or a
hand-written ``kunit.autorun=1`` on the command line. Under autorun, suites
write their KTAP to the kernel log at boot (init-only suites can run
nowhere else); read that output from the kernel journal:

.. code-block:: console

   $ ssh <vm> journalctl --dmesg --output=cat

Service units to query
======================

A run exposes two template units on the guest, which you drive with the
tools in :doc:`guests` (:cmd:`systemctl` ``--host <vm> …`` for the units,
``ssh <vm>`` :cmd:`journalctl` ``…`` for their logs):

- ``kunit@<suite>.service``: one per re-runnable KUnit suite, running the
  suite once. The ``<suite>`` is a directory entry under
  ``/sys/kernel/debug/kunit``, for example ``rust_rxarray`` or
  ``rust_doctests_kernel``.
- ``kunit-results@<suite>.service``: reads back an init-only suite's
  boot-time results (such a suite has no debugfs ``run`` node).

How a flow surfaces its state in the Windmill job log, and why these recipes are
the out-of-band view, is covered in :doc:`guests`.

Starting a suite from the CLI
=============================

The flow's ``start`` step is one ``systemctl start``; the same command runs a
suite without the flow, and the journal carries its KTAP:

.. code-block:: console

   $ systemctl --host <vm> start --no-block kunit@<suite>.service
   $ ssh <vm> journalctl --unit=kunit@<suite>.service --follow

Repeated starts re-run the suite: the unit has no ``RemainAfterExit``, so
each ``start`` writes the ``run`` node again. Results collected this way are
yours to read from the journal; only flow runs are parsed, judged, and
rolled into ``report.json``.

Querying suite status and logs
==============================

List the suites the guest exposes, and the units a run has instantiated:

.. code-block:: console

   $ ssh <vm> ls /sys/kernel/debug/kunit
   $ systemctl --host <vm> list-units 'kunit@*'

Full status of one suite's unit (its state, the last run's result, and the
tail of its journal):

.. code-block:: console

   $ systemctl --host <vm> status kunit@<suite>.service

The ``wait`` step does not poll unit state (a sub-second instance may already
be gone); it reads the run's outcome from the unit's journal past the cursor
``start`` captured, where systemd logs exactly one of ``Finished`` (success)
or ``Failed to start`` per start job. Unit state is still the quickest manual
check while a suite runs: ``ActiveState=activating`` means still running,
``inactive`` is the success terminus and ``failed`` the failure terminus.

.. code-block:: console

   $ systemctl --host <vm> show kunit@<suite>.service --property=ActiveState

The unit's exit status only says whether the results were readable (the
suite exists); the pass/fail verdict is in the KTAP, not any exit code.
Read back the suite's full KTAP after the run (the job log shows this same
KTAP inside the merged unit and kernel journal the ``wait`` step streams):

.. code-block:: console

   $ ssh <vm> journalctl --unit=kunit@<suite>.service --output=cat

Each test's ``ok <n> <name>`` / ``not ok <n> <name>`` line, any ``# SKIP``
directive, and the suite-level verdict line all appear here.

Running a suite by hand
=======================

The unit is a thin wrapper over the kernel's own debugfs interface, the one
`Run tests without kunit_tool`_ documents (`KUnit running tips`_ collects
more recipes around it), so you can re-run a suite and read its result
without any systemd unit at all. A re-run is a **write** to the suite's
``run`` node (reading it only prints a usage string); the last run's KTAP is
always in ``results``:

.. code-block:: console

   $ echo 1 > /sys/kernel/debug/kunit/<suite>/run     # on the guest, as root
   $ cat   /sys/kernel/debug/kunit/<suite>/results

The suite is the granularity: the ``run`` node re-runs every test in it, and
the kernel accepts no per-test filter through debugfs (``kunit.filter_glob``
exists only as a boot-time parameter). Pick suites, not tests, in the flow
too. One re-run caveat from the kernel side: a test must clean up after
itself to run correctly a second time, so a suite that only fails on re-run
is telling you about its own hygiene, not necessarily a regression.

Stopping a run
==============

To abort a suite, stop its unit (the documented fallback in
:src:`f/kunit/stop.py`, and what the flow's ``failure_module`` runs when you
cancel the Windmill job):

.. code-block:: console

   $ systemctl --host <vm> stop         kunit@<suite>.service
   $ systemctl --host <vm> reset-failed kunit@<suite>.service

The ``wait`` step sees the stop in the run's journal and ends that suite.
Cancelling the Windmill job (a clean cancel, not a force-kill of the worker)
runs the ``failure_module`` for you, so it tears the running units down on
the guest; a force-kill bypasses that, and the manual stop above is the
recovery.

A suite whose test oopses the guest takes the VM down; ``wait`` detects this
by crash-checking the host ``qemu-system@<vm>.service`` on each poll, and
``collect`` folds the crash into a failed suite rather than a false pass.
With **Stream Logs** on, whatever oops lines a poll drained before the VM
died are in the job log.

.. _KUnit: https://docs.kernel.org/dev-tools/kunit/
.. _KTAP: https://docs.kernel.org/dev-tools/ktap.html
.. _Run tests without kunit_tool: https://docs.kernel.org/dev-tools/kunit/run_manual.html
.. _KUnit running tips: https://docs.kernel.org/dev-tools/kunit/running_tips.html
