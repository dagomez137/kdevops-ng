.. SPDX-License-Identifier: copyleft-next-0.3.1

====================
Development commands
====================

kdevops-ng does all of its tooling in Nix. Every command below runs from a
pinned toolchain and behaves the same on any host and in CI. Run them from
anywhere inside the checkout.

The quickest way to see what is available is to ask Nix:

.. code-block:: console

   $ nix run                  # print the menu of development commands
   $ nix flake show           # the raw flake outputs (checks, apps, devShells)

Each kind of task uses the Nix command that fits its purpose: read-only
verification is a flake *check*, programs that change, build, serve, or query
the tree are *apps* run with ``nix run``, and the formatter is ``nix fmt``.

The development shell
=====================

``nix develop .#checks`` drops you into a shell with the whole toolchain on
``PATH`` (``ruff``, ``pyright``, ``nixfmt``, ``statix``, ``deadnix``,
``shellcheck``, ``python3``, ``git``). Use it for ad-hoc work, or run a single
tool without entering it:

.. code-block:: console

   $ nix develop .#checks --command ruff check scripts f
   $ nix develop .#docs           # the Sphinx toolchain on its own

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

View the served HTML over an SSH tunnel:
``ssh -L 8001:127.0.0.1:8001 <host>``.

Deploying the Windmill stack
============================

The self-hosted Windmill instance builds and deploys from this flake too, with
``nix run .#windmill-build`` and ``nix run .#windmill-deploy``. See
:doc:`/deployment/nix-backend` for the full procedure, what each service is,
configuration, TLS, workers, and teardown.

Other
=====

.. code-block:: console

   $ nix run .#maintainers -- f/fstests/report.py   # who to Cc for a change
