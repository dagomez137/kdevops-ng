.. SPDX-License-Identifier: copyleft-next-0.3.1

=====================
Run an xfstests check
=====================

The `f/fstests/check`_ flow runs an `xfstests`_ ``./check`` against an
already-booted, fstests-ready guest: the Windmill equivalent of an xfstests
``./check`` run. The guest is produced separately by `f/qsu/bringup`_ with a
writable ``fstests`` virtiofs share mounted at ``/var/lib/xfstests`` and the
test/scratch NVMe drives attached.

The flow is thin and mirrors xfstests/systemd vocabulary one-to-one:

1. ``discover``: gate the guest over vsock-SSH and enumerate its devices and
   ``FSTYP``.
2. ``render_config``: write ``local.config`` (``HOST_OPTIONS``) and the
   ``check.env`` ``EnvironmentFile`` onto the host side of the share. Its
   ``[section]`` names drive the loop.
3. for each ``section`` in turn: ``start`` →  ``wait`` → ``collect``.
4. ``report``: fold the per-section results into one verdict.

On the guest each ``[section]`` runs as a ``xfstests@<section>.service``
template unit started with ``--no-block``, executing ``./check -s <section>``.
The unit sets :cmd:`TimeoutStartSec` to ``infinity``, so a section is never
bounded by systemd's start timeout. The patched ``check`` runs each individual
test inside its own transient scope, ``fstests-<test>.scope`` (for example
``fstests-generic-310.scope``), created with :cmd:`systemd-run` in ``--scope``
mode. This is what makes a single test independently observable and killable
from outside the run.

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
- ``fstests-<test>.scope``: the transient scope wrapping the single test
  currently executing inside that section, for example
  ``fstests-generic-310.scope``.

How a flow surfaces its state in the Windmill job log, and why these recipes are
the out-of-band view, is covered in :doc:`guests`.

Querying section status and logs
================================

List the sections currently running on a guest, and the per-test scope inside
the live section:

.. code-block:: console

   $ systemctl --host <vm> list-units 'xfstests@*'
   $ systemctl --host <vm> list-units --type=scope    # the fstests-<test>.scope

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
continues; it is **0 (no limit) by default**, taking effect only on a guest
built with the patched xfstests. When it is unset, or you want to intervene on
a run already in flight, kill the test by hand: this reproduces exactly what the
watchdog would have done.

The symptom is a section that makes no progress: its journal stops emitting new
``generic/<n>`` lines and ``status`` keeps reporting ``activating`` for far
longer than the test should take. Find the in-flight scope and confirm which
test it is:

.. code-block:: console

   $ systemctl --host <vm> list-units --type=scope

.. code-block:: text

   UNIT                       ACTIVE SUB      DESCRIPTION
   fstests-generic-310.scope  active running  [systemd-run] ... generic/310

Kill that scope:

.. code-block:: console

   $ systemctl --host <vm> kill --signal=SIGKILL fstests-generic-310.scope

``check`` sees the test killed, records it as a failure, and proceeds to the
next test. The failure surfaces as an output mismatch with exit status 137
(128 + SIGKILL), the diff naming the killed ``systemd-run --scope`` command, for
example::

   generic/310  [failed, exit status 137]- output mismatch
     -*** done
     +/tmp/xfstests.XXXXXX/check: line 700: NNNNNN Killed  systemd-run ...

and the run moves on to ``generic/311``.

To abort the **whole** section instead of skipping one test, stop its unit (this
is the documented fallback in `f/fstests/stop.py`_, and also what the flow's
``failure_module`` runs when you cancel the Windmill job):

.. code-block:: console

   $ systemctl --host <vm> stop         xfstests@<section>.service
   $ systemctl --host <vm> reset-failed xfstests@<section>.service

The ``wait`` step observes the unit go inactive and the run ends that section.
Cancelling the Windmill job (a clean cancel, not a force-kill of the worker)
runs the ``failure_module`` for you, so it tears the running section down on the
guest; a force-kill bypasses that and leaves ``./check`` burning CPU under
``TimeoutStartSec=infinity``.

.. _f/fstests/check:
   https://github.com/dagomez137/kdevops-ng/tree/main/f/fstests/check.flow
.. _f/qsu/bringup:
   https://github.com/dagomez137/kdevops-ng/tree/main/f/qsu/bringup.flow
.. _f/fstests/stop.py:
   https://github.com/dagomez137/kdevops-ng/blob/main/f/fstests/stop.py
.. _xfstests: https://git.kernel.org/pub/scm/fs/xfs/xfstests-dev.git/
