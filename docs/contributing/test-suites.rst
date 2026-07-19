.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

==================
Adding test suites
==================

kdevops-ng carries two fully worked test-suite integrations, and every rule
in this page is extracted from them: :doc:`/flows/fstests` (a userland suite
that needs a share, devices, and per-test observation) and
:doc:`/flows/kunit` (an in-kernel suite driven purely through debugfs, no
userland at all). A new suite is one of those two archetypes, or a mix; read
the matching sources alongside this page. The requirements are not
suggestions: each one exists because its absence was a real bug or a real
usability failure in those two integrations.

A suite spans four layers, each owned by a different part of the tree:

1. **Kernel configuration**: fragments in the vendored
   :src:`vendor/linux-config-fragments` project build what the suite tests.
2. **Guest closure**: a NixOS module in the vendored
   :src:`vendor/nixos-flake` project ships the suite's systemd units (and
   its userland, when it has one).
3. **The flow**: a subsystem directory ``f/<suite>/`` with a thin Windmill
   flow composing verb-named Python steps.
4. **Documentation**: a per-flow page under ``docs/flows/``, staged first.

Guest execution model: first-class systemd units
================================================

The unit of execution on the guest is a **templated oneshot service**, one
instance per unit of work, named for what it runs:
``xfstests@<section>.service``
(:src:`vendor/nixos-flake/modules/testSuites/fstests.nix`) and
``kunit@<suite>.service``
(:src:`vendor/nixos-flake/modules/testSuites/kunit.nix`). The flow is the
commander; the unit is the executor. Requirements:

- ``Type=oneshot`` with **no** ``RemainAfterExit``: a repeated
  ``systemctl start`` must re-run the work, and the dead unit's properties
  stay queryable. The upstream shape to copy is ``modprobe@.service``.
- ``TimeoutStartSec=infinity``, with a comment saying why: systemd must
  never bound a run; the flow's ``wait`` step owns the deadline and stops
  the unit on expiry.
- ``StandardOutput=journal+console`` and a ``SyslogIdentifier``: the journal
  is the results channel the flow collects, and the console mirror is the
  crash forensics channel.
- ``StartLimitIntervalSec=0`` and a ``Documentation=`` URL, per the
  ``modprobe@.service`` precedent: a driver may restart one instance in
  quick succession, and a reader must find the interface being wrapped.
- Prefer a **wrapper-free** unit when the kernel interface allows it: the
  kunit templates are two plain ``ExecStart`` commands (a strict trigger
  write, then a results read), with ``StandardInputText`` as the payload
  channel. When the suite needs environment and a working directory, use
  the unit's own mechanisms (``EnvironmentFile=``,
  ``WorkingDirectory=...%v`` so results key by kernel release), as
  ``xfstests@`` does; never a shell string.
- When a trigger can fail while a stale artifact still exists, **split the
  unit**: a strict template whose failed trigger fails the unit, and a
  separate read-back template for work that can only be read, never re-run
  (``kunit@`` versus ``kunit-results@``). A ``-`` prefix that swallows the
  trigger failure converts stale data into a false pass.
- When individual tests inside a run must be independently observable and
  killable, each runs in a **transient scope** via :cmd:`systemd-run`
  ``--scope``. The scope buys three things: the unit name names the
  in-flight test, so ``systemctl list-units --type=scope`` is live
  progress; a hung test dies surgically (``systemctl kill`` of its scope
  takes the test's whole process tree, the runner records the failure and
  the section continues); and a ``RuntimeMaxSec`` property on the scope
  automates that kill as a watchdog, driven by a form knob (fstests'
  Per-test Timeout). Prefer the suite's own upstream support where it
  exists and carry only the delta as a package patch: xfstests' ``check``
  creates ``fs<test>.scope`` itself, feature-detected, and the overlay
  adds only the watchdog
  (:src:`vendor/nixos-flake/overlays/xfstests.nix`).
- Boot-time preparation is its own small unit modeled on upstream's
  :cmd:`kmod-static-nodes`: a ``DefaultDependencies=no`` oneshot, ordered
  ``Before=`` its consumer, deriving runtime config from ``/lib/modules/%v``
  into a ``/run`` drop-in directory, with the same conditions
  (``kunit-test-modules.service`` writes ``/run/modules-load.d/kunit.conf``
  for :cmd:`systemd-modules-load`). No static list where the content is a
  property of the booted kernel.
- Do not add timers, sockets, or path units without a genuine activation
  story; the audits that rejected them for kunit
  (`notes/kunit/full-support-audit.md` in the tree) show the reasoning to
  repeat.
- The unit's exit codes are plumbing, not verdicts. The verdict lives in
  the results the unit emits (KTAP, a xunit report); the flow parses it.
  Know the systemd caveat: a **successful** process exit is journal-logged
  only at debug level, so never require a visible exit-status record to
  call a run clean; the ``Finished`` job record is the proof.
- A runner that reopens its own stdout breaks under
  ``StandardOutput=journal``: the journal stream is a socket, and
  ``open(2)`` on a socket fails with ``ENXIO``, so an append redirection
  to ``/dev/stdout`` (kselftest's default per-test logfile) kills every
  test before it runs while the framing still streams. Prefer the
  runner's own file-logging mode over any wrapper (``run_kselftest.sh
  --summary`` sends the per-test output to a file on the share; the KTAP
  skeleton keeps writing to the inherited journal fd).
- A suite's hard-coded FHS tool paths get :cmd:`systemd-tmpfiles` ``L+``
  compat symlinks, declared in the suite's own module so only its guests
  carry them. The kselftest module ships three: ``/usr/bin/timeout`` (the
  runner's per-test watchdog silently vanishes without it),
  ``/sbin/modprobe`` (every ``module.sh``-driven test skips without it),
  and ``/bin/bash`` for test-script shebangs
  (:src:`vendor/nixos-flake/modules/testSuites/selftests.nix`).

The nix layer
=============

**Guest closure** (:src:`vendor/nixos-flake`, a vendored project with its
own conventions; read its ``CLAUDE.md`` first):

- One module per suite under ``modules/testSuites/``, import-as-gate and
  optionless unless a knob is genuinely needed (then follow the
  ``monitoring.nix`` option pattern). Register it in the vendored
  ``flake.nix`` under ``nixosModules.testSuites`` and in its per-suite eval
  checks, so ``nix flake check`` builds a closure with the module composed.
- Userland comes from nixpkgs, an overlay (``overlays/xfstests.nix`` bumps
  the suite version), or a custom package under ``pkgs/``.
- A userland suite that exchanges files with the host declares a virtiofs
  share (fstests mounts its share at ``/var/lib/xfstests``; the host side
  is ``$WORKERS_DIR/shared/<suite>/<vm>/``). An in-kernel suite needs no
  share: results travel over the SSH transport and the host keeps only its
  own ``report.json``.
- The module names only upstream projects in comments and commit messages;
  the vendored project must stand alone.

**Kernel configuration** (:src:`vendor/linux-config-fragments`, also
vendored with its own rules):

- A fragment enables what the suite tests, tristate ``=m`` by default with
  a self-contained ``builtin/`` mirror setting ``=y``
  (``kernel/configs/test/kunit.config`` and
  ``kernel/configs/builtin/test/kunit.config`` are the pair to copy).
- Policy defaults belong in the fragment and the imageless preset, and
  boot-time behavior the user may want to flip gets both a fragment and a
  curated boot toggle (the ``kunit-autorun.config`` fragment pairs with the
  ``kunit.autorun=1`` entry of the boot form's Kernel Parameters
  multiselect; see :src:`f/qsu/boot.flow`).
- Run ``verify_config.sh`` from the fragments project
  (``vendor/linux-config-fragments/scripts/verify_config.sh``) on a merged
  config before committing a fragment change; the repo's commit rules
  require it.

**Registration**: add the suite to ``_TEST_SUITES`` in
:src:`f/nix/render_config.py` so the closure form offers it as a curated
choice, and note whether it carries a share. After editing anything under
``vendor/``, run ``nix run .#windmill-install`` so the workbench copy the
workers read is re-synced; ``wmill sync push`` deploys the ``f/`` content.

The flow layer
==============

One subsystem directory ``f/<suite>/`` holding verb-named steps and a thin
flow, per the conventions in the repository ``CLAUDE.md``. The canonical
step chain, shared by both suites:

1. ``discover``: gate the guest (``is-system-running``, the unit template
   present, the suite's interface reachable) and enumerate the units of
   work. **Refuse an empty enumeration**: a guest exposing nothing to run
   must fail here, never pass a vacuous run. Write the enumeration to a
   per-VM cache on the shared dir for the form's picker (a form dynselect
   cannot reach the guest); see ``f/kunit/discover.py``.
2. Optional config/prepare steps when the suite needs them:
   ``render_config`` (write the suite's config to the share, its section
   names driving the loop), ``prepare``, ``wipe``, ``reboot``: all fstests
   steps to copy from.
3. A sequential ``forloopflow`` over the work items, each iteration
   ``start`` → ``wait`` → ``collect``, with ``skip_failures: true`` so one
   hard step failure (an SSH blip) does not abort the remaining items; the
   aggregation treats the error object as a failed row.
4. ``report``: pure aggregation into a Windmill ``render_all`` of native
   tables (run info, one row per item, one row per test with failures
   first). ``render_all`` must stay the **sole** key of the returned value
   or the tables do not render. Also write the full rollup to
   ``$WORKERS_DIR/shared/<suite>/<vm>/<kver>/report.json`` atomically.
5. ``judge``: fail the job unless the run passed, so a red run is a red
   Windmill job for schedules and embedding flows. The pass rule lives
   once, as ``run_status`` in the suite's ``common.py``, shared by report
   and judge; on success judge passes the report through unchanged so the
   tables stay the flow result.
6. A ``failure_module`` ``stop`` step that tears the effective run set down
   on cancel or error, idempotently, and never itself fails.

Correctness invariants, each one a former bug:

- **A run must have an identity.** Results read back must provably belong
  to this run, never a previous one. kunit anchors on a guest journal
  cursor captured before ``start`` (unit state is useless: a sub-second
  oneshot instance is garbage-collected before the first poll); fstests
  removes the previous ``result.xml`` from the share before starting, so
  anything present afterwards is this run's.
- **Validate completeness.** Parse the results' own plan/stats and refuse a
  truncated document (``parse_ktap`` checks the ``1..N`` plan and the
  kernel's suite-level stats line); a report that only exists at run end is
  complete by construction once staleness is handled.
- **Gate the verdict on the run outcome.** ``crashed`` and ``timed_out``
  from ``wait`` must reach ``collect`` and force a failure even when a
  plausible report exists.
- **Nothing-ran is not a pass.** Zero items, an empty body, or an all-skip
  run is ``notrun``/failed, never silent success.
- **Host liveness, not guest polling, detects death.** Each ``wait`` poll
  checks the host ``qemu-system@<vm>.service``; any not-alive state (a
  crash or a clean outside stop) ends the wait as ``crashed``. Transient
  SSH failures only retry: the host unit is the authority.
- **Split the worker tags**: quick lifecycle steps on ``vm``, the long
  ``wait`` poll on ``vm-run``, so a hung run never starves control
  operations.

Mechanics: all guest access goes through the shared vsock-SSH transport
(:src:`f/common/remote`), all host execution through the
:src:`f/common/devshell` runners; explicit argv lists, never a shell
string; the runner logs the exact command once, so never hand-print a
mirrored copy; print filesystem mutations (``wrote <path>``). ``wait``
streams the guest's merged unit + kernel journal into the job log each
poll (``stream_logs``), which makes the job log the primary live view.

The UI layer
============

The UI is the first-class interface; the CLI is the same machinery driven
by hand and is documented, not primary.

- **Curated pickers, never raw JSON.** Every choice a kernel developer
  carries in their head becomes a named option: the VM picker
  (``dynselect-list_vms``), the work-item picker
  (``dynmultiselect-list_suites``, a multi-select dropdown), the sections
  catalog. A picker that needs guest state reads the per-VM cache
  ``discover`` writes, with a curated fallback before first discovery, so
  it is never an empty box. Keep a raw config only as a gated advanced
  override (fstests' Edit Local.config toggle).
- **A bounding Service group**: timeout, poll interval, log streaming, with
  suite-appropriate defaults (kunit: 600 s, sub-second suites; fstests: a
  day, hours-long sections).
- Knob names are the upstream tool's own keywords; schema ``title:``
  overrides fix Windmill's auto-casing for acronyms (``CPU``, ``QEMU``,
  ``KTAP``).
- The results are native sortable tables (the ``render_all`` contract),
  and the verdict is the job state (``judge``), so a schedule needs no
  table to know a run failed.

The documentation layer
=======================

Every flow gets a page under ``docs/flows/``, written UI-first: the run
form, watching a run in the job log, where results land, then the CLI
recipes (:cmd:`systemctl` ``--host``, :cmd:`journalctl`, starting a unit by
hand, stopping a run). Content shared by every guest-driving flow lives in
:doc:`/flows/guests` and is linked, never copied. Link upstream
documentation at first mention (the suite's own docs, the systemd units
wrapped, the kernel interfaces used) through the ``cmd_links`` table and
named targets, and link our own unit sources with the ``:src:`` role so a
reader can inspect the exact definitions. New pages land as ``:orphan:``
drafts listed in ``docs/staging.rst`` until reviewed.

Before committing any of it
===========================

``nix flake check`` and ``scripts/check-style.sh`` gate the repository;
the vendored projects carry their own checks (``nix flake check`` in
:src:`vendor/nixos-flake`, ``verify_config.sh`` in the fragments). The
commit rules in ``CLAUDE.md`` apply per tree: atomic commits, subsystem
prefixes, and each vendored project's own message conventions. Validate the
integration live before trusting it: the kunit end-to-end run (build with
the fragment, boot, run, inspect the tables and the streamed journal) is
the model for what "done" means.
