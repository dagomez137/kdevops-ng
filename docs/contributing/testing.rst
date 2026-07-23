.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

========================
Testing flows and steps
========================

A change to the workspace content is verified in three layers, ordered by
when they run: the hermetic fixture tests inside the source gate, before
every commit; the preview smoke suite against the running instance, before
a deploy; and the deploy-time selfchecks Windmill runs by itself, after
every deploy. The first is part of :doc:`development`'s gate, the second is
one command, and the third needs no command at all. Together they make a
contract regression visible at the earliest point it can be seen, and they
are the checks to run before submitting a change.

The fixture tests
=================

The suite under :src:`tests` covers the pure logic of the ``f/`` step
modules: the KTAP and xunit parsers, the shared ``run_status`` verdict
rules, the per-suite catalogs and their regexes, the store index's pure
reads, the directory-resolution chain, and the bringup resolve step. The
degrade paths are the point: a missing report, a truncated document,
disagreeing totals, a dangling index entry, an unresolvable environment.
The tests run hermetically, with no instance and no network, as the
``tests`` flake check, so ``nix flake check`` fails on a broken contract
before anything is deployed:

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ nix build .#checks.x86_64-linux.tests        # just the fixture tests
   $ nix develop .#checks --command pytest tests  # directly, while iterating

When a change adds or alters a parser, a verdict rule, or any pure read,
extend the fixtures in the same change; assert the degrade behavior, not
only the happy path.

The preview smoke suite
=======================

The fixture tests cover the pure logic; the smoke suite covers the same
contracts end to end, as Windmill preview jobs on the real workers, without
deploying anything:

.. code-block:: console
   :class: cmd-host

   $ nix run .#preview-smoke
   $ nix run .#preview-smoke -- --only fstests    # narrow while iterating

For every test-suite flow it runs the ``collect``, ``report``, and
``judge`` modules in isolation (:src:`scripts/preview-smoke.py`), with
fixture and degrade arguments that touch no guest and no share: the report
cases omit ``vm_name``, which skips the rollup write, and the collect case
names a VM that cannot exist. The asserted contracts are the ones the flows
lean on: a crashed run can never read as a pass, an empty run is failed,
``report`` returns ``render_all`` as the sole key, and ``judge`` fails a
red run while passing a green report through unchanged.

Beyond the suites, the smoke set covers the pure probe and resolve steps
of the build and bringup flows: a fake identity must read absent from the
store index, an empty pick set must resolve to an empty manifest, and a
reuse mode without a pick must refuse.

Two caveats frame how to read a result. A preview runs the local step body,
but its ``from f...`` imports resolve against the deployed workspace copy,
so a shared-module change reaches previews only after the next
``wmill sync push``; a red case can therefore mean the deployed copy lags a
local fix, which is the harness catching real skew, not a false alarm. And
previews execute on the real workers, so any case added here must stay
read-only or carry arguments that cannot touch live state.

Run the smoke suite before a deploy that touches flow steps, and again
after the push: the second run also proves the deployed copies match.

The deploy-time selfchecks
==========================

The same contracts re-verify themselves on every deploy, with Windmill's
own CI-test mechanism. Each suite carries a ``selfcheck`` step (for
example :src:`f/fstests/selfcheck`) whose leading ``# test:`` annotation
registers it as a CI test for the suite's flow and scripts; the annotation
must be the very first line of the file, ahead of the SPDX tag, because
the backend reads only the opening comment block. When a deploy updates an
annotated flow or script, Windmill runs the selfcheck automatically: the
step drives the deployed ``collect``, ``report``, and ``judge`` through
the shared case table in :src:`f/common/selfcheck` and fails loudly when a
contract broke, so the deployment itself is badged red in the run history.

There is nothing to invoke by hand. A new suite joins the mechanism by
adding its own thin ``selfcheck`` step naming its flow and script glob,
one call into the shared library; see :doc:`test-suites`.

Ad-hoc step previews
====================

For exploratory testing beyond the fixed smoke cases, the ``wmill`` CLI
runs local, undeployed content directly. ``wmill lint`` validates every
flow definition offline, ``wmill script preview`` runs a local script
body, and ``wmill flow preview`` runs a local flow definition; its
``--step`` flag runs one module in isolation with explicit arguments,
which is the fastest way to exercise a step's degrade paths against real
worker state:

.. code-block:: console
   :class: cmd-host

   $ nix run .#wmill -- lint
   $ nix run .#wmill -- script preview f/common/store.py --silent
   $ nix run .#wmill -- flow preview f/fstests/check.flow --step collect \
       --data '{"vm_name": "no-such-vm", "section": "xfs_4k",
                "kernel_version": "0.0.0-test"}' --silent

The preview caveats above apply here unchanged: deployed shared imports,
real workers.

Before submitting
=================

The order that catches problems earliest:

.. code-block:: console
   :class: cmd-host

   $ nix run .#format                                        # format first
   $ nix flake check                              # gate: lint, tests, drift
   $ nix develop .#checks --command bash scripts/check-style.sh
   $ nix run .#preview-smoke                      # when flow steps changed

The first three are commit rule 6 and gate every commit. The smoke suite
is the extra step when a change touches flow steps; after the deploy, the
selfchecks re-run the contracts without being asked, and a rerun of the
smoke suite confirms the deployed and local copies agree.
