.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

====================
Run kernel selftests
====================

The :src:`f/selftests/run` flow runs the kernel's own selftests
(`kselftest`_, the ``tools/testing/selftests`` tree) on an already-booted
guest: the Windmill equivalent of ``run_kselftest.sh``, one systemd unit per
collection. Unlike xfstests, the test binaries do not come from a package:
kselftests are version-coupled to the kernel under test and are built from
the same source tree by :src:`f/kernel/build` when its **Selftests** group
is enabled. That build publishes the self-contained install tree
(``run_kselftest.sh``, ``kselftest-list.txt``, one directory per
collection) to the Nix store as ``kselftests-<release>``, and this flow
lays it on the guest's writable ``selftests`` share, keyed by kernel
release. The guest is produced by :src:`f/qsu/bringup` with the
``selftests`` test suite in its closure, which ships the
``kselftest@.service`` executor templates (all guest-side units come from
one vendored module,
:src:`vendor/nixos-flake/modules/testSuites/selftests.nix`).

The flow is thin and mirrors kselftest/systemd vocabulary one-to-one:

1. ``discover``: gate the guest over vsock-SSH, confirm the
   ``kselftest@.service`` template is present, resolve the
   ``kselftests-<release>`` store artifact matching the guest's booted
   kernel (failing loudly when the kernel was built without the
   **Selftests** group), and enumerate the installed collections from
   ``kselftest-list.txt``. A tree exposing zero collections fails here
   rather than passing an empty run.
2. ``prepare``: copy the install tree from the read-only store onto the
   writable share (tests run inside their collection directory and write
   there), skipping when the share already carries this release's tree.
3. ``render_config``: write ``kselftest.env``, the environment file the
   units read, carrying the runner flags (the per-test timeout override).
4. for each ``collection`` in turn: ``start`` → ``wait`` → ``collect``.
5. ``report``: fold the per-collection results into one verdict.
6. ``judge``: fail the job unless every collection passed, so a red run is
   a red Windmill job.

On the guest each collection runs as a template unit started with
``--no-block``: ``kselftest@<collection>.service`` executes
``run_kselftest.sh --collection <collection>``. The runner emits one flat
`KTAP`_ document to the journal: an up-front ``1..N`` plan, one
``ok N selftests: <collection>: <test>`` line per test (with ``# SKIP``,
``# XFAIL``, ``# TIMEOUT`` or ``# exit=<rc>`` directives), and a closing
``# Totals:`` line. The verdict is that KTAP: the units pass
``--no-error-on-fail``, so the unit's exit status reports only
infrastructure errors (an unknown collection, a missing runner), never a
test failure, and ``collect`` checks the plan against the parsed result
lines so a journal truncated mid-run can never read as a pass.

Before starting the unit, ``start`` captures the guest journal's end-of-now
cursor: everything after it belongs to this run, so a re-run can never
report a previous run's results. The unit sets :cmd:`TimeoutStartSec` to
``infinity``, exactly as the xfstests and KUnit executors do: the whole run
is bounded by the ``wait`` step's own deadline, while each *individual*
test is bounded by kselftest's upstream watchdog (below).

Every step carries a worker tag: the quick lifecycle and control steps run
on the ``vm`` tag, and the ``wait`` poll runs on the ``vm-run`` tag, so a
hung collection never starves a quick control op. See
:doc:`../deployment/nix`.

The run form
============

The UI is the first-class way to run collections; the CLI sections further
down are the same machinery driven by hand.

**Collections** picks the collections to run, by name, from the installed
tree's ``kselftest-list.txt``; empty runs every installed collection. Which
collections exist was already curated at build time by the kernel build's
**Selftests** ``targets`` knob, so the default run is the built set. A form
dropdown cannot reach the guest, so the picker reads the per-VM cache the
flow's ``discover`` step writes on each run; before the first discovery it
falls back to the curated set. **Tests** is the advanced override: explicit
``collection:test`` entries (the ``kselftest-list.txt`` line format), each
run as its own unit through ``kselftest-test@``; when set, it replaces the
collections selection.

**Per-test Timeout** maps to the runner's ``--override-timeout``. Left at
0, each collection's own upstream ``settings`` timeout applies (45 seconds
by default; some collections such as ``timers`` declare ``timeout=0``,
unbounded). A non-zero value bounds every individual test at that many
seconds; an overrunning test is killed by :cmd:`timeout` and recorded as
``# TIMEOUT``, and the run continues. ``kmod`` ships no ``settings`` file
upstream, so its module-loader stress dies at the 45-second default; it
completes in about four minutes, so run it with a Per-test Timeout of 300
seconds or more. ``firmware`` is the opposite: it carries an upstream
``settings`` timeout of 165 seconds sized to its own fallback-handshake
math, so the default already bounds it and no override is needed.

Two collections need guest state the closure provides. The module-driven
tests (``sysctl``, ``lib``, ``kmod``, ``module``) load kernel modules
through the ``/sbin/modprobe`` compat symlink the suite module ships. The
``firmware`` collection additionally needs a ``/lib/firmware`` mount
point to exist: its namespace sub-test mounts a tmpfs there before any
other test runs, so the module creates the directory (the tests supply
their own firmware from temporary directories, so nothing lives in it).
Both are set up by :src:`vendor/nixos-flake/modules/testSuites/selftests`
and need no operator action.

The **Service** group bounds the run. **Item Timeout** (default 900
seconds) is ``wait``'s per-collection deadline; on expiry the unit is
stopped. It is the only bound on a test that survives the per-test
watchdog (a test that masks signals can), so a hung collection costs at
most this budget before the run moves on. **Poll Interval** (default 15
seconds) paces the status polls and journal drains. **Stream Logs**
(default on) prints the guest's merged unit and kernel journal into the
job log on each poll, so the KTAP and dmesg are visible live.

Watching a run
==============

The job log is the primary live view (:doc:`guests` explains why): open the
running ``wait`` step and the guest's KTAP and kernel messages stream in as
each collection runs. When the run finishes, the ``report`` step renders
three sortable tables (run info, one row per collection, one row per test
with failures first), and the ``judge`` step turns the verdict into the job
state. The same rollup lands on the host side of the share as
``report.json``, keyed by the guest's kernel release:

.. code-block:: console

   $ cat "$WORKERS_DIR/shared/selftests/<vm>/<kver>/report.json"

Building and shipping the selftests
===================================

kselftests are built from the kernel tree being tested, so the pipeline is:

- **Build**: enable the **Selftests** group in :src:`f/kernel/build` (or in
  :src:`f/qsu/bringup`'s kernel component). ``targets`` curates which
  collections to build; the default is the portable syscall-level set
  (``seccomp``, ``cgroup``, ``futex``, ``timers``, ``sysctl``, ``lib`` and
  friends). The build runs the upstream ``kselftest-install`` make target
  with ``FORCE_TARGETS=1``, so a collection that fails to build fails the
  step instead of silently vanishing from the install.
- **Publish**: the install tree lands in the Nix store as
  ``kselftests-<release>``, GC-rooted in the store index and fetchable by
  peers like any other build artifact (see :doc:`/concepts/build-store`).
- **Ship**: this flow's ``prepare`` copies the tree onto the guest's
  writable share. The binaries resolve their libraries through the guest's
  read-only ``/nix/store`` mount, so nothing else is installed.

The kernel-side prerequisites are carried by the
``test/kselftest.config`` fragment of the vendored
:src:`vendor/linux-config-fragments` project, and the imageless preset
already includes them, so a default preset kernel passes the curated set.
``discover`` enforces the version coupling: the store artifact is resolved
by the guest's booted ``uname -r``, so a guest booted with a different
kernel than the built selftests fails before anything runs.

Service units to query
======================

A run exposes two template units on the guest, which you drive with the
tools in :doc:`guests` (:cmd:`systemctl` ``--host <vm> …`` for the units,
``ssh <vm>`` :cmd:`journalctl` ``…`` for their logs):

- ``kselftest@<collection>.service``: one per collection, running
  ``run_kselftest.sh --collection <collection>``.
- ``kselftest-test@<collection>:<test>.service``: one single test, running
  ``run_kselftest.sh --test <collection>:<test>``.

A collection name can carry ``/`` (``net/forwarding``) or ``-``
(``cpu-hotplug``), so the unit instance is the :cmd:`systemd-escape` form
of the name (``net-forwarding``, ``cpu\x2dhotplug``); the unit's ``%I``
specifier restores the literal name. The flow escapes for you; only the
manual recipes below need it.

How a flow surfaces its state in the Windmill job log, and why these
recipes are the out-of-band view, is covered in :doc:`guests`.

Starting a collection from the CLI
==================================

The flow's ``start`` step is one ``systemctl start``; the same command runs
a collection without the flow, and the journal carries its KTAP:

.. code-block:: console

   $ systemctl --host <vm> start --no-block kselftest@seccomp.service
   $ ssh <vm> journalctl --unit=kselftest@seccomp.service --follow

For a name that needs escaping, let :cmd:`systemd-escape` produce the
instance:

.. code-block:: console

   $ systemctl --host <vm> start --no-block \
       kselftest@"$(systemd-escape 'net/forwarding')".service

Repeated starts re-run the collection: the unit has no ``RemainAfterExit``,
so each ``start`` invokes the runner again. List what the installed tree
can run straight from the share, or from the guest:

.. code-block:: console

   $ cat "$WORKERS_DIR/shared/selftests/<vm>/<kver>/tree/kselftest-list.txt"
   $ ssh <vm> /var/lib/kselftests/<kver>/tree/run_kselftest.sh --list

Querying collection status and logs
===================================

List the units a run has instantiated, and the status of one collection:

.. code-block:: console

   $ systemctl --host <vm> list-units 'kselftest@*'
   $ systemctl --host <vm> status kselftest@seccomp.service

``ActiveState=activating`` means the collection is still running,
``inactive`` is the success terminus and ``failed`` the failure terminus.
Remember the exit-status caveat: with ``--no-error-on-fail`` a *failed
unit* means the runner itself could not do its job; test failures leave the
unit successful and live in the KTAP. Read a run's full KTAP back from the
journal:

.. code-block:: console

   $ ssh <vm> journalctl --unit=kselftest@seccomp.service --output=cat

The per-test watchdog is upstream kselftest machinery, not systemd: the
runner wraps every test in :cmd:`timeout` with the collection's
``settings`` value (or the form's **Per-test Timeout** override), and a
killed test appears as ``not ok <n> … # TIMEOUT <secs> seconds`` in that
same journal, while the run moves on to the next test.

In-kernel module tests beyond kselftest
=======================================

A third family of runtime test suites is covered by neither kselftest nor
KUnit: the classic module-init test modules (the XArray, Maple Tree,
rhashtable, IDA and friends), where loading the module runs the whole
suite and the load's outcome plus the kernel log carry the verdict. Those
run through their own flow, :doc:`runtime-tests`, driving systemd's
upstream ``modprobe@.service`` per module. The lib tests that used to sit
beside them (``printf``, ``scanf``, ``prime_numbers``) were converted to
KUnit upstream and run through :doc:`kunit` on a KUnit-enabled guest
instead.

Stopping a run
==============

To abort a collection, stop its unit (the documented fallback in
:src:`f/selftests/stop.py`, and what the flow's ``failure_module`` runs
when you cancel the Windmill job):

.. code-block:: console

   $ systemctl --host <vm> stop         kselftest@seccomp.service
   $ systemctl --host <vm> reset-failed kselftest@seccomp.service

The ``wait`` step sees the stop in the run's journal and ends that
collection. Cancelling the Windmill job (a clean cancel, not a force-kill
of the worker) runs the ``failure_module`` for you; a force-kill bypasses
that, and the manual stop above is the recovery.

A test that oopses the guest takes the VM down; ``wait`` detects this by
crash-checking the host ``qemu-system@<vm>.service`` on each poll, and
``collect`` folds the crash into a failed collection rather than a false
pass. With **Stream Logs** on, whatever oops lines a poll drained before
the VM died are in the job log. Note that several collections load test
modules by design (``lib``, ``sysctl``), so the guest kernel carrying a
``TAINT_TEST`` taint after a run is expected, not a symptom.

.. _kselftest: https://docs.kernel.org/dev-tools/kselftest.html
.. _KTAP: https://docs.kernel.org/dev-tools/ktap.html
