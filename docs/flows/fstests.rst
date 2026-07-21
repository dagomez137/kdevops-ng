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

1. ``discover``: gate the guest over vsock-SSH and enumerate its devices and
   ``FSTYP``.
2. ``render_config``: write ``local.config`` (``HOST_OPTIONS``) and the
   ``check.env`` ``EnvironmentFile`` onto the host side of the share. Its
   ``[section]`` names drive the loop.
3. for each ``section`` in turn: ``start`` →  ``wait`` → ``collect``.
   ``start`` first removes the section's previous ``result.xml`` from the
   share (xfstests writes it only at run end), so a run that crashes or never
   finishes can never inherit an old report as a false pass.
4. ``report``: fold the per-section results into one verdict.
5. ``judge``: fail the job unless every section passed, so a red run is a red
   Windmill job.

Devices and external-device sections
====================================

The guest's NVMe data disks (``/dev/nvme*n1``, whole-disk namespaces) are the
test material. ``discover`` enumerates them in the guest's block-device list
order, and ``render_config`` maps that ordered list onto the xfstests device
roles positionally, per selected section, then writes the paths into the
section's ``local.config``. The catalog itself is device-agnostic (no device
paths), so the same catalog runs on any guest:

.. code-block:: text

   device 0  ->  TEST_DEV       (the persistent fs at /media/test)
   device 1  ->  SCRATCH_DEV    (the mkfs-per-test fs at /media/scratch)
   device 2  ->  TEST_RTDEV  or TEST_LOGDEV      (external sections only)
   device 3  ->  SCRATCH_RTDEV or SCRATCH_LOGDEV (external sections only)
   last      ->  LOGWRITES_DEV  (reserved first, if the replay log is on)

A plain section uses the first two (or pools the extras as ``SCRATCH_DEV_POOL``
with more than two). An **external-device** section (an XFS realtime or
external-log profile, such as ``xfs_realtime_rtx2_bs4k_ss4k``) attaches its
extra device to **both** filesystems, so the persistent ``TEST_DEV`` mount at
``/media/test`` is itself a realtime (or external-log) filesystem, not just the
scratch one. Such a section carries ``USE_EXTERNAL=yes`` (the canonical xfstests
switch) and declares the canonical device variables empty in the catalog:
``TEST_RTDEV=`` / ``SCRATCH_RTDEV=`` for realtime, or the ``LOGDEV`` pair for an
external log. ``render_config`` fills the empty variables in place from the
discovered devices, and ``prepare`` formats ``TEST_DEV`` with the matching
``-r rtdev=`` / ``-l logdev=`` (xfstests formats only the scratch device itself,
so the test device would otherwise have no realtime/external-log section).

An external section therefore needs four NVMe drives (a test and a scratch
device plus their external device), or five when the ``LOGWRITES_DEV`` replay
log is on, which is why :src:`f/qsu/bringup` attaches five drives by default. A
guest with too few drives skips the section (with the shortfall named) rather
than running it wrong. Confirm a realtime section landed from the ``realtime``
line of the device's ``xfs_info`` (``rtextents`` non-zero) or the ``rtdev=``
mount option on ``/media/test``.

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
