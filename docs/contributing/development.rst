.. SPDX-License-Identifier: copyleft-next-0.3.1

====================
Development commands
====================

kdevops-ng does all of its tooling in Nix. Every command below runs from a
pinned toolchain and behaves the same on any host and in CI. Run them from
anywhere inside the checkout.

``nix flake show`` lists the runnable apps with their descriptions, and a bare
``nix run`` prints a short pointer to the essentials. This page is the full
reference.

.. code-block:: console

   $ nix flake show           # apps, packages, checks, shells, formatter
   $ nix run                  # a short pointer to the gate, formatter, and list

Each kind of task uses the Nix command that fits its purpose: read-only
verification is a flake *check*, programs that change, serve, or query the tree
are *apps* run with ``nix run``, the Windmill components are *packages* built
with ``nix build .#<name>``, and the formatter is ``nix fmt``.

These apps are workspace-bound task runners, not portable programs: each one
changes into the checkout and acts on it, so it is run as ``nix run .#<name>``
from inside the repository, not as ``nix run github:owner/kdevops-ng#<name>``
from anywhere. This is a deliberate choice of ``apps`` as the task interface in
place of a Makefile.

The development shell
=====================

``nix develop`` drops you into the default shell: the checks toolchain plus
``wmill``, the workspace CLI, so the tools come from Nix rather than a host
install. ``nix develop .#checks`` is the same toolchain without ``wmill``
(``ruff``, ``pyright``, ``nixfmt``, ``statix``, ``deadnix``, ``shellcheck``,
``python3``, ``git``), and ``nix develop .#docs`` is the Sphinx toolchain on its
own. Use a shell for ad-hoc work, or run a single tool without entering it:

.. code-block:: console

   $ nix develop --command wmill --version       # wmill from Nix, not the host
   $ nix develop .#checks --command ruff check scripts f
   $ nix develop .#docs --command sphinx-build --version

Verifying
=========

``nix flake check`` is the gate. It runs every read-only check the flake
defines: ``ruff`` lint and format verification, generated-file drift, the
fixture tests, and tree formatting. CI runs the same single command.

.. code-block:: console

   $ nix flake check                              # the whole source gate
   $ nix build .#checks.x86_64-linux.lint         # just the ruff check
   $ nix build .#checks.x86_64-linux.generated    # just the drift check
   $ nix build .#checks.x86_64-linux.tests        # just the fixture tests

The fixture tests under ``tests/`` cover the pure logic of the ``f/`` step
modules (the KTAP and xunit parsers, the shared verdict rules, the store
index's pure reads) with no instance and no network, so a change that breaks
a parsing contract or a degrade path fails the gate before it is deployed.
Run them directly from the checks shell while iterating:

.. code-block:: console

   $ nix develop .#checks --command pytest tests

The whitespace, end-of-file, and commit-trailer checks need the git repository,
so they cannot be a sandboxed flake check; run them from the checks shell:

.. code-block:: console

   $ nix develop .#checks --command bash scripts/check-style.sh

Before committing (commit rule 6), run both: ``nix flake check`` and the
``check-style.sh`` pass above.

Formatting
==========

``nix fmt`` formats the whole tree in one pass: ``nixfmt`` for Nix and ``ruff``
for Python, at the line length in ``pyproject.toml``. It only formats; to
*verify* formatting use ``nix flake check`` (the ``formatting`` check), never
``nix fmt --check``, which is not a flag.

.. code-block:: console

   $ nix fmt                  # format Nix and Python in place
   $ nix run .#format         # ruff lint-fix (import order) and format Python
   $ nix run .#reflow         # rewrap wmill description fields to clean blocks

Type-checking
=============

``pyright`` runs from the checks shell. It is advisory: it is not part of the
gate, because a Windmill step's ``main()`` annotations are the UI form schema
rather than ordinary typing.

.. code-block:: console

   $ nix develop .#checks --command pyright

Previewing flows against the instance
=====================================

The fixture tests cover the pure logic; the ``wmill`` CLI covers the rest by
running local, undeployed content against the running instance as preview
jobs. ``wmill lint`` validates every flow definition offline, ``wmill script
preview`` runs a local script body, and ``wmill flow preview`` runs a local
flow definition; its ``--step`` flag runs one module in isolation with
explicit arguments, which is the fastest way to exercise a step's degrade
paths against real worker state:

.. code-block:: console

   $ nix run .#wmill -- lint
   $ nix run .#wmill -- script preview f/common/store.py --silent
   $ nix run .#wmill -- flow preview f/fstests/check.flow --step collect \
       --data '{"vm_name": "no-such-vm", "section": "xfs_4k",
                "kernel_version": "0.0.0-test"}' --silent

Two caveats. A preview runs the local step body, but its ``from f...``
imports resolve against the deployed workspace copy, so a shared-module
change is visible to previews only after the next ``wmill sync push``. And a
preview executes on the real workers, so preview only read-only steps, or
mutating steps with arguments that cannot touch live state.

``nix run .#preview-smoke`` packages that workflow as the standing smoke
suite: for every test-suite flow it previews the ``collect``, ``report``,
and ``judge`` modules in isolation with fixture and degrade arguments that
touch no guest and no share, and asserts the contracts the flows lean on (a
crashed run can never read as a pass, an empty run is failed, ``report``
returns ``render_all`` as the sole key, ``judge`` fails a red run and
passes a green report through unchanged). ``--only <substring>`` narrows
the run while iterating on one suite or step. Because of the import caveat
above, a red case can also mean the deployed copy of a shared module lags a
local fix; that is the harness catching real skew, not a false alarm.

The same contracts re-verify themselves on every deploy: each suite
carries a ``selfcheck`` step whose leading ``# test:`` annotation registers
it as a Windmill CI test, so pushing the flow or any of the suite's scripts
runs the deployed ``collect``/``report``/``judge`` against the shared
fixture cases in :src:`f/common/selfcheck` and badges the deployment red
when a contract broke.

Documentation
=============

.. code-block:: console

   $ nix run .#docs           # render reStructuredText to docs/_build/html
   $ nix run .#serve -- 8001  # serve the built HTML on 127.0.0.1:8001

Open it at ``http://127.0.0.1:8001``; if the host is remote, forward the port
first with ``ssh -L 8001:127.0.0.1:8001 <host>``.

Deploying the Windmill stack
============================

The self-hosted Windmill instance builds and deploys from this flake too, with
``nix run .#windmill-build`` and ``nix run .#windmill-deploy``. See
:doc:`/deployment/nix` for the full procedure, what each service is,
configuration, TLS, workers, and teardown.

The Nix store
=============

Builds accumulate in ``/nix/store``. ``nix store gc`` reclaims space by deleting
store paths nothing roots. The deploy out-links under
``~/.local/state/windmill/pkgs`` are GC roots, as is any ``result`` symlink a
``nix build`` leaves, so the collector keeps the builds they point at. To free a
build, remove its out-link or ``result`` first, then collect:

.. code-block:: console

   $ nix store gc                 # delete unrooted store paths
   $ rm result && nix store gc    # drop a build, then reclaim it

Other
=====

.. code-block:: console

   $ nix run .#maintainers -- f/fstests/report.py   # who to Cc for a change
