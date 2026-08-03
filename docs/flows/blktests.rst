.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

====================
Run a blktests check
====================

The :src:`f/blktests/check` flow runs a `blktests`_ ``./check`` against an
already-booted, blktests-ready guest: the Windmill equivalent of a blktests
``./check`` run. The guest is produced separately by :src:`f/qsu/bringup` with
a writable ``blktests`` virtiofs share mounted at ``/var/lib/blktests``.

The flow is thin and mirrors blktests/systemd vocabulary one-to-one:

1. :src:`discover <f/blktests/discover>`: gate the guest over vsock-SSH and
   enumerate its test groups and NVMe devices.
2. :src:`render_config <f/blktests/render_config>`: write the suite's one
   ``config`` file (blktests' own sourced configuration, every form knob a
   config variable under its upstream name) and, per selected group, its own
   ``EnvironmentFile`` onto the host side of the share. The group names drive
   the loop.
3. for each ``group`` in turn: :src:`wipe <f/blktests/wipe>` →
   :src:`start <f/blktests/start>` → :src:`wait <f/blktests/wait>` →
   :src:`collect <f/blktests/collect>`. ``start`` first removes the group's
   previous result files from the share (blktests writes each test's result
   only at test end), so a run that crashes or never finishes can never
   inherit an old result as a false pass.
4. :src:`report <f/blktests/report>`: fold the per-group results into one
   verdict.
5. :src:`judge <f/blktests/judge>`: fail the job unless every group passed, so
   a red run is a red Windmill job.

Devices
=======

Most of blktests brings its own devices: the ``loop``, ``nbd``, ``throtl``,
``ublk``, ``srp``, ``rnbd`` and ``blktrace`` groups, and the majority of
``block`` and ``nvme``, create null_blk instances, scsi_debug disks, loopback
files, NVMe fabrics targets or NBD servers as they run, so the zero-config
run needs no test device at all.

The **Test Devs** form field maps to blktests' ``TEST_DEVS`` config variable:
real block devices (the guest's spare ``/dev/nvme*n1`` disks, as enumerated by
``discover``) that the device-driven tests then run against, once per device.
Those tests are **destructive** to the named devices, which is why the field
is empty by default and pairs with the **Wipe Devices** knob (``wipefs`` and
``blkdiscard`` before the run, exactly as the fstests flow wipes its disks).
A test that needs a device while ``TEST_DEVS`` is empty is simply not run,
blktests' own behavior. **Device Only** (``DEVICE_ONLY``) inverts the focus
and runs only the device tests.

blktests owns device setup
==========================

The host never creates a null_blk instance, configures an nvmet target, or
formats anything; ``./check`` and the test scripts do it all, and each test
restores what it changed (module unloads, sysfs queue attributes, cgroup
state) before the next one runs. The flow's only device mutation is the
optional pre-run wipe of the ``TEST_DEVS`` disks.

On the guest each selected group runs as a ``blktests@<group>.service``
template unit started with ``--no-block``, executing ``check`` for exactly
that group with ``--config=/var/lib/blktests/config`` and
``--output=/var/lib/blktests/<kver>/results``. The unit sets
:cmd:`TimeoutStartSec` to ``infinity``, so a group is never bounded by
systemd's start timeout. The packaged ``check`` carries a patch
(:src:`vendor/nixos-flake/pkgs/blktests-runtime-max-sec.patch`) that runs
each individual test inside its own transient scope,
``blktests-<group>-<nnn>.scope``, which is what makes a single test
independently observable and killable from outside the run; upstream blktests
has no scope support of its own.

Every step carries a worker tag: the quick lifecycle and control steps run on
the ``vm`` tag, and the long-lived ``wait`` poll runs on the ``vm-run`` tag,
so a long run never starves a quick control op.

Service units to query
======================

A run exposes two kinds of systemd object on the guest, which you drive with
the tools in :doc:`guests` (``systemctl --host <vm> …`` for the units,
``ssh <vm> journalctl …`` for their logs):

- ``blktests@<group>.service``: one per selected group, running ``check``
  for that group. The ``<group>`` is the blktests group directory name, for
  example ``blktests@loop.service``.
- ``blktests-<group>-<nnn>.scope``: the transient scope wrapping the single
  test currently executing, for example ``blktests-block-002.scope``.

How a flow surfaces its state in the Windmill job log, and why these recipes
are the out-of-band view, is covered in :doc:`guests`.

Querying group status and logs
==============================

List the groups currently running on a guest, and the per-test scope inside
the live group:

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ systemctl --host <vm> list-units 'blktests@*'
   $ systemctl --host <vm> list-units --type=scope    # blktests-<test>.scope

The three properties the ``wait`` step polls to decide a group is done are
``Result``, ``ExecMainStatus`` and ``ActiveState``; read them the same way:

.. code-block:: console
   :class: cmd-host

   $ systemctl --host <vm> show blktests@<group>.service \
       --property=Result --property=ExecMainStatus --property=ActiveState

``ActiveState=activating`` means the group is still running, ``inactive`` or
``failed`` is terminal. Follow the live journal of a group, the same stream
the job log shows:

.. code-block:: console
   :class: cmd-host

   $ ssh <vm> journalctl --unit=blktests@<group>.service --follow

Each test prints a start line when it begins and its verdict line when it
ends, and ``check`` also writes a ``run blktests <group>/<nnn>`` marker into
the kernel log at every test start, so the merged journal the job log streams
names the in-flight test at all times. On a failure the journal carries the
output diff, the dmesg excerpt, or the exit status, whichever failed the
test.

Where the run lives on the guest
================================

Everything a run reads and writes lives under ``/var/lib/blktests`` on the
guest, the writable ``blktests`` virtiofs share the guest mounts from the
host. The same files are visible host-side at
``$WORKERS_DIR/shared/blktests/<vm>/``, so the flow lays the config down and
reads the results back through the shared directory rather than copying
anything over the SSH transport: the guest and the host see one directory.

The files on the share (guest paths shown; each is visible host-side under
the same name) are:

- ``config``: the suite's own sourced configuration file, written by
  ``render_config`` from the form. Every knob keeps its upstream name
  (``TEST_DEVS``, ``QUICK_RUN``, ``TIMEOUT``, ``EXCLUDE``,
  ``NVMET_TRTYPES``, and the rest), so the file reads exactly like the
  ``config`` documented by blktests itself, plus the watchdog pair
  ``TEST_TIMEOUT``/``TEST_TIMEOUTS`` the carried patch reads. The gated
  **Edit Config** override replaces the rendered file wholesale.
- ``<group>.env``: the unit's per-instance ``EnvironmentFile`` (read as
  ``%i.env``), also written by ``render_config``. It holds only
  ``BLKTESTS_ARGS``, the positional argument list ``check`` receives: the
  group name, or an explicit test list scoped to that group.
- ``<kver>/results/``: blktests' own ``--output`` tree under one kernel
  release (``%v``). Each test writes one status file,
  ``<devdir>/<group>/<nnn>``, a small key/value record carrying ``status``
  (``pass``, ``fail`` or ``not run``), the failure ``reason`` (``output``,
  ``exit``, ``dmesg`` or ``kmemleak``) and the ``runtime``, beside its
  ``.full`` log and, on failure, the ``.out.bad`` diff and ``.dmesg``
  excerpt. ``<devdir>`` is ``nodev`` for self-contained tests, the device
  basename for ``TEST_DEVS`` tests, and grows a variant suffix when a test
  repeats per transport or backend (``nodev_tr_tcp_bd_file``), so one test
  number can yield several result rows.
- ``<kver>/report.json``: the folded run verdict ``report`` writes.

A group whose prerequisites fail writes **no files at all** and exits zero;
``collect`` treats a group with zero result files as not run and the run as
failed, never as a silent pass.

Running a group by hand
=======================

``render_config`` writes the shared ``config`` once and a ``<group>.env`` per
selected group, which the systemd unit reads via ``%i``. Any armed group is
therefore a one-command run, no shared active-config to swap and no
re-render. List the armed groups and start one, following its journal:

.. code-block:: console
   :caption: guest
   :class: cmd-guest

   # ls /var/lib/blktests/*.env
   # systemctl start blktests@loop.service
   # journalctl --unit=blktests@loop.service --follow

To try one test or a subset against a group without a flow round-trip, edit
its ``<group>.env`` and start. The env carries the one knob ``check``
receives positionally:

.. code-block:: text

   BLKTESTS_ARGS=block/002 block/005

``BLKTESTS_ARGS`` is the verbatim ``check`` positional tail: a group name
runs the whole group, explicit ``group/nnn`` names run exactly those tests
(and, per blktests' own semantics, a test named explicitly bypasses the
``EXCLUDE``, ``QUICK_RUN`` and ``DEVICE_ONLY`` filters). The tunables live in
the shared ``config`` and apply to every group alike; edit that file to flip
``QUICK_RUN``, add a ``TEST_DEVS`` entry, or arm the watchdog. systemd
re-reads the ``EnvironmentFile`` on each start, so edit, then restart:

.. code-block:: console
   :class: cmd-guest

   # systemctl restart blktests@loop.service

The flow is the canonical path anyway: it regenerates the config and every
env from the form, so by-hand edits are a scratch pad, overwritten on the
next run.

Restarting a hung test
======================

A single test can wedge: a livelock, or a thread stuck in uninterruptible
sleep. blktests' own ``TIMEOUT`` is advisory (only tests that opt in honor
it), and the group unit is ``TimeoutStartSec=infinity``, so nothing bounds
that one test unless the per-test watchdog is armed. The **Per-test Timeout**
form field (``test_timeout`` → ``TEST_TIMEOUT``) sets each test's scope
:cmd:`RuntimeMaxSec` through the carried patch, so systemd kills an
overrunning test and the run continues; it is **0 (no limit) by default**.
When it is unset, or you want to intervene on a run already in flight, kill
the test by hand: this reproduces exactly what the watchdog would have done.

The symptom is a group that makes no progress: its journal stops emitting new
test lines and ``status`` keeps reporting ``activating`` for far longer than
the test should take. Find the in-flight scope and confirm which test it is:

.. code-block:: console
   :class: cmd-host

   $ systemctl --host <vm> list-units --type=scope

.. code-block:: text
   :class: cmd-guest

   UNIT                       ACTIVE SUB      DESCRIPTION
   blktests-block-002.scope   active running  blktests block/002

Stop that scope (systemd sends SIGTERM to everything in it; the test's
processes die with it, while the patched runner handles the signal, records
the test as failed, and proceeds to the next test):

.. code-block:: console
   :class: cmd-host

   $ systemctl --host <vm> stop blktests-block-002.scope

To abort the **whole** group instead of skipping one test, stop its unit
(this is what the flow's ``failure_module`` runs when you cancel the Windmill
job; :src:`f/blktests/stop` also clears any lingering per-test scope, which
lives outside the service's control group and so survives a bare unit stop):

.. code-block:: console
   :class: cmd-host

   $ systemctl --host <vm> stop         blktests@<group>.service
   $ systemctl --host <vm> reset-failed blktests@<group>.service

The ``wait`` step observes the unit go inactive and the run ends that group.
Cancelling the Windmill job (a clean cancel, not a force-kill of the worker)
runs the ``failure_module`` for you, so it tears the running group down on
the guest; a force-kill bypasses that and leaves ``check`` running under
``TimeoutStartSec=infinity``.

.. _blktests: https://github.com/linux-blktests/blktests
