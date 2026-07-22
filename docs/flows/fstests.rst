.. SPDX-License-Identifier: copyleft-next-0.3.1

=====================
Run an xfstests check
=====================

The :src:`f/fstests/check` flow runs an `xfstests`_ ``./check`` against an
already-booted, fstests-ready guest: the Windmill equivalent of an xfstests
``./check`` run. The guest is produced separately by :src:`f/qsu/bringup` with a
writable ``fstests`` virtiofs share mounted at ``/var/lib/xfstests`` and the
test/scratch NVMe drives attached.

The flow is thin and mirrors xfstests/systemd vocabulary one-to-one:

1. :src:`discover <f/fstests/discover>`: gate the guest over vsock-SSH and
   enumerate its devices and ``FSTYP``.
2. :src:`render_config <f/fstests/render_config>`: write ``local.config``
   (``HOST_OPTIONS``) and the ``check.env`` ``EnvironmentFile`` onto the host
   side of the share. Its ``[section]`` names drive the loop.
3. for each ``section`` in turn: :src:`start <f/fstests/start>` →
   :src:`wait <f/fstests/wait>` → :src:`collect <f/fstests/collect>`. ``start``
   first removes the section's previous ``result.xml`` from the share (xfstests
   writes it only at run end), so a run that crashes or never finishes can never
   inherit an old report as a false pass.
4. :src:`report <f/fstests/report>`: fold the per-section results into one
   verdict.
5. :src:`judge <f/fstests/judge>`: fail the job unless every section passed, so
   a red run is a red Windmill job.

Devices
=======

The guest's NVMe data disks (``/dev/nvme*n1``) are the raw material.
``discover`` lists them in the guest's block-device order, and ``render_config``
assigns them to the xfstests device roles by position, writing the paths into
each section's ``local.config``. The catalog carries no device paths, so it runs
unchanged on any guest:

.. code-block:: text

   device 0  ->  TEST_DEV       persistent fs, mounted at /media/test
   device 1  ->  SCRATCH_DEV    throwaway fs, remade per test at /media/scratch
   device 2  ->  TEST_RTDEV  / TEST_LOGDEV       external-device sections only
   device 3  ->  SCRATCH_RTDEV / SCRATCH_LOGDEV  external-device sections only
   last      ->  LOGWRITES_DEV  dm-log-writes replay log, when enabled

A plain XFS section uses the first two disks (any extras become a
``SCRATCH_DEV_POOL``). An **external-device** section, an XFS realtime or
external-log profile such as ``xfs_realtime_rtx2_bs4k_ss4k``, gives *both*
filesystems a dedicated realtime or log volume: the test filesystem at
``/media/test`` is then itself realtime, not just the scratch one. Such a
section needs four disks, or five with the log-writes device, which is why
:src:`f/qsu/bringup` attaches five by default. A guest with too few is short a
role, so ``render_config`` skips that section rather than run it wrong.

xfstests owns formatting and mounting
=====================================

The host never runs :cmd:`mkfs` or :cmd:`mount`; ``./check`` does it all. It
remakes ``SCRATCH_DEV`` before every test, mounts and unmounts both filesystems
itself, attaches each realtime or log volume through its own ``mkfs`` and
``-o rtdev=`` mount, and remakes ``TEST_DEV`` once per section when the
**Recreate TEST_DEV** form knob is on (the default; off reuses the existing test
filesystem, which ``./check`` then only mounts).
:src:`prepare <f/fstests/prepare>` just lays down the section's
``local.config``, creates the mount points, and loads the filesystem driver.

Because ``./check`` builds the filesystems, the run records their *realized*
geometry, not the configured intent. At the end of the section ``wait``
snapshots each device's :cmd:`xfs_info`, plus the ``MKFS_OPTIONS`` and
``MOUNT_OPTIONS`` lines xfstests echoes in its own run header (the commands it
ran, ``rtdev=`` and all), to ``<section>.geometry.json``. That header is read
from the journal scoped to the run's systemd invocation id, so a re-run of the
same section unit never matches a previous run's lines. A realtime ``TEST_DEV``
shows ``rtextents`` non-zero, which the report's per-section table carries
alongside the ``mkfs`` and ``mount`` commands; a realtime or log volume shows
``xfs_info``'s "not a valid XFS filesystem" message, confirming it is a raw
external volume rather than a broken filesystem.

On the guest each ``[section]`` runs as a ``xfstests@<section>.service``
template unit started with ``--no-block``, executing ``./check -s <section>``.
The unit sets :cmd:`TimeoutStartSec` to ``infinity``, so a section is never
bounded by systemd's start timeout. Upstream ``check`` runs each individual
test inside its own transient scope, ``fs<test>.scope`` (for example
``fsgeneric-310.scope``), created with :cmd:`systemd-run` in ``--scope`` mode
when the guest has systemd; the xfstests overlay
(:src:`vendor/nixos-flake/overlays/xfstests.nix`) adds the per-test watchdog
on top. The scope is what makes a single test independently observable and
killable from outside the run.

Every step carries a worker tag: the quick lifecycle and control steps run on
the ``vm`` tag, and the long-lived ``wait`` poll runs on the ``vm-run`` tag, so
a long run never starves a quick control op. The ``vm-run`` worker instance
count is the concurrent-test-run cap; see :doc:`../deployment/nix`.

Service units to query
======================

A run exposes two kinds of systemd object on the guest, which you drive with the
tools in :doc:`guests` (``systemctl --host <vm> …`` for the units, ``ssh <vm>
journalctl …`` for their logs):

- ``xfstests@<section>.service``: one per ``[section]``, running
  ``./check -s <section>``. The ``<section>`` is the name as it appears in
  ``local.config``, for example ``xfs_realtime_rtx2_bs4k_ss4k``.
- ``fs<test>.scope``: the transient scope wrapping the single test
  currently executing inside that section, for example
  ``fsgeneric-310.scope``.

How a flow surfaces its state in the Windmill job log, and why these recipes are
the out-of-band view, is covered in :doc:`guests`.

Querying section status and logs
================================

List the sections currently running on a guest, and the per-test scope inside
the live section:

.. code-block:: console

   $ systemctl --host <vm> list-units 'xfstests@*'
   $ systemctl --host <vm> list-units --type=scope    # the fs<test>.scope

Full status of one section (the cgroup line shows the running ``./check`` and
the current test's helper processes):

.. code-block:: console

   $ systemctl --host <vm> status xfstests@<section>.service

The three properties the ``wait`` step polls to decide a section is done are
``Result``, ``ExecMainStatus`` and ``ActiveState``; read them the same way:

.. code-block:: console

   $ systemctl --host <vm> show xfstests@<section>.service \
       --property=Result --property=ExecMainStatus --property=ActiveState

``ActiveState=activating`` means the section is still running, ``active`` or
``failed`` is terminal; ``Result`` carries systemd's outcome enum
(``success`` / ``exit-code`` / ``signal`` / ``timeout`` / ...). Follow the live
journal of a section, the same stream the job log shows:

.. code-block:: console

   $ ssh <vm> journalctl --unit=xfstests@<section>.service --follow

Each test's progress line (``generic/310``, then its elapsed seconds), its
``[failed, ...]`` verdict, and the ``.out.bad`` path on a mismatch all appear
here. The per-section results, the ``.out.bad`` diffs and the ``check.log`` also
land on the host side of the share under
``$WORKERS_DIR/shared/fstests/<vm>/<kver>/`` once ``collect`` runs, and the
folded run verdict is written to ``report.json`` in that directory.

Where the run lives on the guest
================================

Everything a run reads and writes lives under ``/var/lib/xfstests`` on the
guest, the writable ``fstests`` virtiofs share the guest mounts from the host.
The same files are visible host-side at ``$WORKERS_DIR/shared/fstests/<vm>/``,
so the flow lays the config down and reads the results back through the shared
directory rather than copying anything over the SSH transport: the guest and the
host see one directory.

The ``xfstests@<section>.service`` template unit reads its inputs from that
directory at start time. The files on the share (guest paths shown; each is
visible host-side under the same name) are:

- ``local.config``: the active single-section xfstests ``HOST_OPTIONS`` config
  for the section running now. It carries that one section's ``FSTYP``,
  ``MKFS_OPTIONS`` and ``MOUNT_OPTIONS`` plus the injected device roles
  (``TEST_DEV``, ``SCRATCH_DEV``, ``TEST_RTDEV`` and the rest for that section).
  ``prepare`` writes it by copying the section's ``<section>.config`` over it.
- ``check.env``: the unit's ``EnvironmentFile``, written by ``render_config``.
  It holds ``HOST_OPTIONS=/var/lib/xfstests/local.config``,
  ``XFSTESTS_CHECK_ARGS`` (the ``./check`` flags: the ``-g`` groups, a test
  list, ``-R`` report, and the rest of the test selection), and
  ``RECREATE_TEST_DEV`` (``true``/``false``).
- ``<section>.config``: one device-bound single-section config per section
  selected in the last run, also written by ``render_config``. ``prepare``
  activates one of these as ``local.config``.
- ``<kver>/results/<section>/``: the results tree for one section under one
  kernel release, holding ``result.xml``, ``check.log`` and the per-test
  ``<test>.full``, ``<test>.out.bad`` and ``<test>.dmesg``. The
  ``xfstests-check`` wrapper forces ``RESULT_BASE=$PWD/results`` and the unit's
  ``WorkingDirectory`` is ``/var/lib/xfstests/%v`` (``%v`` is the guest's kernel
  release), so results are keyed by kernel version.

The unit runs ``xfstests-check -s <section> $XFSTESTS_CHECK_ARGS``; the wrapper
execs xfstests' ``./check`` in the guest, reading ``HOST_OPTIONS`` (that is,
``local.config``). It is ``Type=oneshot`` with ``TimeoutStartSec=infinity`` and
logs to ``journal+console`` under the ``xfstests`` syslog identifier.

Running a section by hand
=========================

``local.config`` and ``check.env`` are single global files on the share, so
exactly one section is armed at a time: whichever one the last run (or
``prepare``) left in ``local.config``. ``systemctl cat`` resolves for any
instance name, but ``systemctl start xfstests@<section>.service`` only produces
a correct run when ``local.config`` currently holds that section, because the
unit reads its ``HOST_OPTIONS`` from ``local.config`` regardless of the instance
name.

To run a different already-rendered section by hand, activate its config with
:cmd:`cp` first (exactly what ``prepare`` does), then start the unit:

.. code-block:: console

   $ cp /var/lib/xfstests/<section>.config /var/lib/xfstests/local.config
   $ systemctl start xfstests@<section>.service

To change the test selection by hand, edit ``XFSTESTS_CHECK_ARGS`` in
``/var/lib/xfstests/check.env`` and restart the unit; systemd re-reads the
``EnvironmentFile`` on each start:

.. code-block:: console

   $ systemctl restart xfstests@<section>.service

The normal path is to re-run the flow, which regenerates both ``local.config``
and ``check.env`` from the form and cannot leave them inconsistent.

Restarting a hung test
======================

A single test can wedge: a livelock, or a thread stuck in uninterruptible
sleep. Because the section unit is ``TimeoutStartSec=infinity``, nothing bounds
that one test unless the per-test watchdog is armed. The **Per-test Timeout**
form field (``test_timeout`` → ``TEST_TIMEOUT``) sets each test's scope
:cmd:`RuntimeMaxSec`, so systemd kills an overrunning test and the run
continues; it is **0 (no limit) by default**, taking effect on a guest whose
xfstests carries the overlay's watchdog. When it is unset, or you want to
intervene on a run already in flight, kill the test by hand: this reproduces
exactly what the watchdog would have done.

The symptom is a section that makes no progress: its journal stops emitting new
``generic/<n>`` lines and ``status`` keeps reporting ``activating`` for far
longer than the test should take. Find the in-flight scope and confirm which
test it is:

.. code-block:: console

   $ systemctl --host <vm> list-units --type=scope

.. code-block:: text

   UNIT                  ACTIVE SUB      DESCRIPTION
   fsgeneric-310.scope   active running  [systemd-run] ... generic/310

Kill that scope:

.. code-block:: console

   $ systemctl --host <vm> kill --signal=SIGKILL fsgeneric-310.scope

``check`` sees the test killed, records it as a failure, and proceeds to the
next test. The failure surfaces as an output mismatch with exit status 137
(128 + SIGKILL), the diff naming the killed ``systemd-run --scope`` command, for
example::

   generic/310  [failed, exit status 137]- output mismatch
     -*** done
     +/tmp/xfstests.XXXXXX/check: line 700: NNNNNN Killed  systemd-run ...

and the run moves on to ``generic/311``.

To abort the **whole** section instead of skipping one test, stop its unit (this
is the documented fallback in :src:`f/fstests/stop.py`, and also what the flow's
``failure_module`` runs when you cancel the Windmill job):

.. code-block:: console

   $ systemctl --host <vm> stop         xfstests@<section>.service
   $ systemctl --host <vm> reset-failed xfstests@<section>.service

The ``wait`` step observes the unit go inactive and the run ends that section.
Cancelling the Windmill job (a clean cancel, not a force-kill of the worker)
runs the ``failure_module`` for you, so it tears the running section down on the
guest; a force-kill bypasses that and leaves ``./check`` burning CPU under
``TimeoutStartSec=infinity``.

.. _xfstests: https://git.kernel.org/pub/scm/fs/xfs/xfstests-dev.git/
