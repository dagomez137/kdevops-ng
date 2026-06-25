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
defines: ``ruff`` lint and format verification, generated-file drift, and tree
formatting. CI runs the same single command.

.. code-block:: console

   $ nix flake check                              # the whole source gate
   $ nix build .#checks.x86_64-linux.lint         # just the ruff check
   $ nix build .#checks.x86_64-linux.generated    # just the drift check

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
:doc:`/deployment/nix-backend` for the full procedure, what each service is,
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
