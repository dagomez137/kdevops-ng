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
   :cmd:`EnvironmentFile` onto the host side of the share. The group names
   drive the loop.
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

The **TEST_DEVS** form field maps to blktests' config variable of the same
name: real block devices (the guest's spare ``/dev/nvme*n1`` disks, as
enumerated by ``discover``) that the device-driven tests then run against,
once per device. Those tests are **destructive** to the named devices, which
is why the field is empty by default and pairs with the **Wipe Devices** knob
(:cmd:`wipefs` and :cmd:`blkdiscard` at the start of each group, exactly as
the fstests flow wipes its disks). A test that needs a device while
``TEST_DEVS`` is empty is simply not run, blktests' own behavior.
**DEVICE_ONLY** inverts the focus and runs only the device tests.

blktests owns device setup
==========================

The host never creates a null_blk instance, configures an nvmet target, or
formats anything; ``./check`` and the test scripts do it all, and each test
restores what it changed (module unloads, sysfs queue attributes, cgroup
state) before the next one runs. The flow's only device mutation is the
optional per-group wipe of the ``TEST_DEVS`` disks.

On the guest each selected group runs as a ``blktests@<group>.service``
template unit (:src:`vendor/nixos-flake/modules/testSuites/blktests.nix`)
started with ``--no-block``, executing ``check`` for exactly
that group with ``--config=/var/lib/blktests/config`` and
``--output=/var/lib/blktests/<kver>/results``. The unit sets
:cmd:`TimeoutStartSec` to ``infinity``, so a group is never bounded by
systemd's start timeout. The packaged ``check`` carries a patch
(:src:`vendor/nixos-flake/pkgs/blktests-runtime-max-sec.patch`) that runs
each individual test inside its own transient scope,
``blktests-<group>-<nnn>.scope``, which is what makes a single test
independently observable and killable from outside the run; upstream blktests
has no scope support of its own.

Service units to query
======================

A run exposes the two kinds of systemd object :doc:`guests` describes:

- ``blktests@<group>.service``: one per selected group, running ``check``
  for that group. The ``<group>`` is the blktests group directory name, for
  example ``blktests@loop.service``.
- ``blktests-<group>-<nnn>.scope``: the transient scope wrapping the single
  test currently executing, for example ``blktests-block-002.scope``.

Listing, querying and stopping those units, and why the Windmill job log is
the primary view of a run, are covered in :doc:`guests`. What is particular
to blktests is what its journal carries: each test prints a start line when
it begins and its verdict line when it ends, and ``check`` also writes a
``run blktests <group>/<nnn>`` marker into the kernel log at every test
start, so the merged journal the job log streams names the in-flight test at
all times. On a failure the journal carries the output diff, the dmesg
excerpt, or the exit status, whichever failed the test.

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
  **Edit config** override replaces the rendered file wholesale.
- ``<group>.env``: the unit's per-instance ``EnvironmentFile`` (read as
  ``%i.env``), also written by ``render_config``. It holds only
  ``BLKTESTS_ARGS``, the positional argument list ``check`` receives: the
  group name, or an explicit test list scoped to that group.
- ``<kver>/results/``: blktests' own ``--output`` tree under one kernel
  release (``%v``). Each test writes one status file,
  ``<devdir>/<group>/<nnn>``, a small key/value record carrying ``status``
  (``pass``, ``fail`` or ``not run``), the failure ``reason`` (``output``,
  ``exit``, ``dmesg`` or ``kmemleak``) and the ``runtime``, beside its
  ``.full`` log and, on failure, the ``.out.bad`` diff, ``.dmesg`` excerpt
  or ``.kmemleak`` report. ``<devdir>`` is ``nodev`` for self-contained
  tests, the device
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

A single test can wedge, and :doc:`guests` covers the general procedure:
spot the stalled run, find its in-flight scope, and stop that scope to skip
the one test.

Two things are particular to blktests. Its own ``TIMEOUT`` is advisory, since
only tests that opt in honor it, so nothing bounds a wedged test by default.
The **Per-test Timeout** form field (``test_timeout`` → ``TEST_TIMEOUT``)
arms the watchdog: through the carried patch it sets each test's scope
:cmd:`RuntimeMaxSec`, so systemd kills an overrunning test and the run
continues. It is **0 (no limit) by default**.

The scopes are named for the test they wrap, so the listing names it outright:

.. code-block:: text
   :class: cmd-guest

   UNIT                       ACTIVE SUB      DESCRIPTION
   blktests-block-002.scope   active running  blktests block/002

:src:`f/blktests/stop` also clears any lingering per-test scope, which lives
outside the service's control group and so survives a bare unit stop.

.. _blktests: https://github.com/linux-blktests/blktests
