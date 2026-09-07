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
2. :src:`render_config <f/fstests/render_config>`: write, per selected section,
   its one-section ``HOST_OPTIONS`` config and its own ``EnvironmentFile`` onto
   the host side of the share. Its ``[section]`` names drive the loop.
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
each section's own config. The catalog carries no device paths, so it runs
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
:src:`prepare <f/fstests/prepare>` just creates the mount points and loads the
filesystem driver; each unit reads its section's own config through its env
file, so nothing needs activating.

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

Service units to query
======================

A run exposes the two kinds of systemd object :doc:`guests` describes:

- ``xfstests@<section>.service``: one per ``[section]``, running
  ``./check -s <section>``. The ``<section>`` is the xfstests section name, for
  example ``xfs_realtime_rtx2_bs4k_ss4k``.
- ``fs<test>.scope``: the transient scope wrapping the single test
  currently executing inside that section, for example
  ``fsgeneric-310.scope``.

Listing, querying and stopping those units, and why the Windmill job log is the
primary view of a run, are covered in :doc:`guests`. What is particular to
xfstests is what its journal carries: each test's progress line
(``generic/310``, then its elapsed seconds), its ``[failed, ...]`` verdict, and
the ``.out.bad`` path on a mismatch all appear there.

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ ssh <vm> journalctl --unit=xfstests@<section>.service --follow

The per-section results, the ``.out.bad`` diffs and the ``check.log`` also land
on the host side of the share under ``$WORKERS_DIR/shared/fstests/<vm>/<kver>/``
once ``collect`` runs, and the folded run verdict is written to ``report.json``
in that directory.

Where the run lives on the guest
================================

Everything a run reads and writes lives under ``/var/lib/xfstests`` on the
guest, the writable ``fstests`` virtiofs share the guest mounts from the host.
The same files are visible host-side at ``$WORKERS_DIR/shared/fstests/<vm>/``,
so the flow lays the config down and reads the results back through the shared
directory rather than copying anything over the SSH transport: the guest and the
host see one directory.

The ``xfstests@<section>.service`` template unit reads its inputs from that
directory at start time, each keyed by the section name, so every instance is
self-contained. The files on the share (guest paths shown; each is visible
host-side under the same name) are:

- ``<section>.config``: that section's one-section xfstests ``HOST_OPTIONS``
  config, written by ``render_config``, one per section selected in the run. It
  carries the section's ``FSTYP``, ``MKFS_OPTIONS`` and ``MOUNT_OPTIONS`` plus
  the injected device roles (``TEST_DEV``, ``SCRATCH_DEV``, ``TEST_RTDEV`` and
  the rest for that section).
- ``<section>.env``: the unit's per-instance ``EnvironmentFile`` (read as
  ``%i.env``), also written by ``render_config``. It holds
  ``HOST_OPTIONS=/var/lib/xfstests/<section>.config`` (the section's own config
  above), ``XFSTESTS_CHECK_ARGS`` (the ``./check`` flags: the ``-g`` groups, a
  test list, ``-R`` report, and the rest of the test selection), and
  ``RECREATE_TEST_DEV`` (``true``/``false``). Being per-instance, each section
  carries its own config path and its own flags.
- ``<kver>/results/<section>/``: the results tree for one section under one
  kernel release, holding ``result.xml``, ``check.log`` and the per-test
  ``<test>.full``, ``<test>.out.bad`` and ``<test>.dmesg``. The
  ``xfstests-check`` wrapper forces ``RESULT_BASE=$PWD/results`` and the unit's
  ``WorkingDirectory`` is ``/var/lib/xfstests/%v`` (``%v`` is the guest's kernel
  release), so results are keyed by kernel version.

The unit reads ``EnvironmentFile=-/var/lib/xfstests/%i.env`` (systemd expands
``%i`` to the section) and runs ``xfstests-check -s <section>
$XFSTESTS_CHECK_ARGS``; the wrapper execs xfstests' ``./check`` in the guest,
reading ``HOST_OPTIONS`` from that section's own ``<section>.config``. It is
``Type=oneshot`` with ``TimeoutStartSec=infinity`` and logs to
``journal+console`` under the ``xfstests`` syslog identifier.

Running a section by hand
=========================

``render_config`` arms every geometry-valid section by default, so the guest is
a complete bench: each section already carries its own ``<section>.config`` and
``<section>.env`` on the share, which the systemd unit reads via ``%i``. Any
section is therefore a one-command run with its own devices and flags, no
shared active-config to swap and no re-render. List the armed sections and
start one, following its journal:

.. code-block:: console
   :caption: guest
   :class: cmd-guest

   # ls /var/lib/xfstests/*.env
   # systemctl start xfstests@<section>.service
   # journalctl --unit=xfstests@<section>.service --follow

``systemctl cat`` resolves for any instance name (the unit is a template), but
a ``start`` only runs once that section's ``.config`` and ``.env`` are on the
share; with **Arm all sections** off, only the sections a run executed are
armed.

To try one test or group against a section without a flow round-trip, edit its
``<section>.env`` and start. The env carries the three knobs ``./check`` reads:

.. code-block:: text

   HOST_OPTIONS=/var/lib/xfstests/<section>.config
   XFSTESTS_CHECK_ARGS=-R xunit generic/362 generic/363
   RECREATE_TEST_DEV=true

``XFSTESTS_CHECK_ARGS`` is the verbatim ``./check`` tail, so narrow it to what
you are chasing: a test list (``generic/362 generic/363``), a group
(``-g quick``), iterations (``-I 5``), and so on; an armed-only section ships
with ``-g auto``. ``RECREATE_TEST_DEV=true`` remakes ``TEST_DEV`` for the run,
``false`` reuses the existing filesystem. Leave ``HOST_OPTIONS`` alone (it
points at the section's own device-bound config). systemd re-reads the
``EnvironmentFile`` on each start, so edit, then restart:

.. code-block:: console
   :class: cmd-guest

   # systemctl restart xfstests@<section>.service

The mount points and the filesystem driver come from ``prepare``, a flow step,
so a section run purely by hand after a fresh boot wants those in place first;
re-running the flow is the simplest way to (re)create them. The flow is the
canonical path anyway: it regenerates every section's config and env from the
form, so by-hand edits are a scratch pad, overwritten on the next run.

Restarting a hung test
======================

A single test can wedge, and :doc:`guests` covers the general procedure: spot
the stalled run, find its in-flight scope, and stop that scope to skip the one
test. The **Per-test Timeout** form field (``test_timeout`` → ``TEST_TIMEOUT``)
arms the watchdog that does it for you on a deadline, taking effect on a guest
whose xfstests carries the overlay's patch; it is **0 (no limit) by default**.

Two details are particular to xfstests. Its scopes are named for the test they
wrap, so the listing names it outright:

.. code-block:: text
   :class: cmd-guest

   UNIT                  ACTIVE SUB      DESCRIPTION
   fsgeneric-310.scope   active running  [systemd-run] ... generic/310

And ``./check`` needs the harder signal, so kill the scope rather than stopping
it:

.. code-block:: console
   :class: cmd-host

   $ systemctl --host <vm> kill --signal=SIGKILL fsgeneric-310.scope

``check`` sees the test killed, records it as a failure, and proceeds to the
next test. The failure surfaces as an output mismatch with exit status 137
(128 + SIGKILL), the diff naming the killed ``systemd-run --scope`` command, for
example:

.. code-block:: text
   :class: cmd-guest

   generic/310  [failed, exit status 137]- output mismatch
     -*** done
     +/tmp/xfstests.XXXXXX/check: line 700: NNNNNN Killed  systemd-run ...

and the run moves on to ``generic/311``.

.. _xfstests: https://git.kernel.org/pub/scm/fs/xfs/xfstests-dev.git/
