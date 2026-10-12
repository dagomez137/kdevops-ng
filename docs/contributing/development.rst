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
   :caption: host
   :class: cmd-host

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
``python3``, ``git``), ``nix develop .#docs`` is the Sphinx toolchain on its
own, and ``nix develop .#vendor`` carries what the vendored projects' checks
run (below). Use a shell for ad-hoc work, or run a single tool without entering
it:

.. code-block:: console
   :class: cmd-host

   $ nix develop --command wmill --version       # wmill from Nix, not the host
   $ nix develop .#checks --command ruff check scripts f
   $ nix develop .#docs --command sphinx-build --version

Verifying
=========

``nix flake check`` is the gate. It runs every read-only check the flake
defines: ``ruff`` lint and format verification, generated-file drift, the
fixture tests, tree formatting, ``statix`` and ``deadnix`` over the root and
``deploy/`` Nix files (with ``nixfmt`` for ``deploy/``, which treefmt leaves to
the deploy flake), ``shellcheck`` over the shell scripts, and a build of every
app, which runs ``writeShellApplication``'s own ``shellcheck`` on it.

.. code-block:: console
   :class: cmd-host

   $ nix flake check                              # the whole source gate
   $ nix build .#checks.x86_64-linux.lint         # just the ruff check
   $ nix build .#checks.x86_64-linux.generated    # just the drift check
   $ nix build .#checks.x86_64-linux.tests        # just the fixture tests
   $ nix build .#checks.x86_64-linux.nix-lint     # just statix and deadnix

The ``tests`` check is the hermetic layer of the flow-testing story;
:doc:`testing` covers both layers (the fixture tests and the
``preview-smoke`` app) and when to run each before submitting.

The whitespace, end-of-file, commit-trailer, prose (:doc:`writing`), rule (3b
to 3d), rst-width and SPDX checks need the git repository, so they cannot be a
sandboxed flake check; run them from the checks shell:

.. code-block:: console
   :class: cmd-host

   $ nix develop .#checks --command bash scripts/check-style.sh

Before committing (commit rule 6), run both: ``nix flake check`` and the
``check-style.sh`` pass above.

The trailer check reads ``HEAD``, so run afterwards it can only report a
commit already made. Linked as the repository's commit-msg hook,
``scripts/commit-msg`` runs the same check on the message before the commit
lands, in the checks devShell. A hook is a file of yours, so link it once by
hand, from anywhere in the checkout:

.. code-block:: console
   :class: cmd-host

   $ ln --symbolic --relative \
       "$(git rev-parse --show-toplevel)/scripts/commit-msg" \
       "$(git rev-parse --git-path hooks)/commit-msg"

Under a global ``core.hooksPath`` that directory serves every repository, so
link per repository only. A merge's default message and a ``--fixup`` commit
carry no sign-off; pass ``--signoff``, or ``--no-verify`` for a commit the
rebase will fold away.

Checking a series
=================

``nix run .#check`` runs the checks on each commit of a range, each in its own
detached worktree with that commit's flake, scripts and docs, so a series
passes only when every commit in it does:

.. code-block:: console
   :class: cmd-host

   $ nix run .#check                     # <base>..HEAD
   $ nix run .#check -- HEAD~3..HEAD     # any range git understands
   $ nix run .#check -- --tip            # only the last commit, while iterating

Commits run in parallel, a quarter of the CPUs' worth at a time
(``--jobs``), as do the checks of one commit, and report in series order.
``--tip`` checks only the range's last commit, with every vendored project the
range touches. Use it for a quick answer while working, and run the full range
before sending.

On each commit it runs, from the commit's tree:

- ``nix flake check`` (above);
- ``check-style.sh``, whose trailer check then reads that commit;
- the Sphinx build with warnings as errors;
- for a commit that touches a vendored project, that project's own checklist,
  through ``scripts/check-vendor.sh`` in the ``vendor`` shell:
  ``vendor/nixos-flake/docs/verifying.md`` (``nixfmt``, its flake check, the
  two templates, every package), ``vendor/qemu-system-units/docs/verifying.md``
  (render every template, ``systemd-analyze verify`` and ``security``,
  ``bash -n``), and for ``vendor/linux-config-fragments`` the README's minimal
  VM set merged with the touched fragments onto a pinned kernel source, then
  ``scripts/verify_config.sh``.

When the range touches Python, ``pyright`` runs once on the base and once on
the tip, and each error the tip adds is listed under the commit that
``git blame`` names for its line. It is advisory: the run never fails on
them. Results are kept by tree id, so a rerun or b4's next patch reuses them.

It prints every command before running it; a check whose script a commit does
not have yet is reported as skipped. Worktrees and logs live under
``.git/kdevops-ng-check/``; a passing run removes its own, a failing one keeps
them for the logs. What it cannot judge stays with review: the commit message
prose, and the ``systemd-analyze security`` table it prints.

The base is ``upstream/main`` when a remote named ``upstream`` exists, else
``origin/main``. It is the ref as you last fetched it, so fetch that remote
before checking a series, or pass the range yourself.

With b4
-------

``nix develop`` carries :cmd:`b4` from upstream master
(``nix/overlays/b4.nix``), and the project's ``.b4-config`` sets
``prep-perpatch-check-cmd`` to ``scripts/check.py --b4``. So ``b4 prep
--check`` runs the same checks on each patch of a series. b4 checks only a
branch it manages, so enroll the branch once; ``nix run .#check`` needs no
enrolling:

.. code-block:: console
   :class: cmd-host

   $ nix develop
   $ git config b4.prep-cover-strategy branch-description   # once, optional
   $ b4 prep --enroll upstream/main                         # or origin/main
   $ b4 prep --check

``--enroll`` makes the current branch a b4 series that starts at the base. b4
pipes each patch to the check on standard input, without its commit, so
``check.py`` finds the commit in ``<base>..HEAD`` by ``git patch-id``. It prints
one ``ERROR:`` line per failed check, which b4 counts as a failure under that
patch, and one line per new ``pyright`` error charged to that patch, which b4
counts as a warning. b4 shows a patch only once its check ends, so while it
waits, ``check.py`` writes each check's start and exit to the terminal. The
cover-strategy setting keeps b4's cover letter in the branch description instead
of an empty commit at the bottom of the series; b4 reads it only from your own
git configuration, never from ``.b4-config``. Under b4's default, ``commit``,
enrolling resets the branch to the base, adds the cover letter as an empty
commit and picks your commits back on top; the commit-msg hook lets that commit
through, and the check skips it.

b4 refuses to check a tree with uncommitted changes to tracked files
(untracked files do not count). It keeps each patch's result in
``~/.cache/b4`` for 30 days, keyed by the check command and the commit, and
shows a cached result without running the check again: a commit that kept its
hash after a rebase is not rechecked, even when ``check.py`` changed or the
cached failure came from something outside the commit. Rerun every patch with:

.. code-block:: console
   :class: cmd-host

   $ b4 prep --check --no-cache

``b4 send`` asks before sending a series whose ``--check`` never ran, but not
whether it passed.

Formatting
==========

``nix fmt`` formats the whole tree in one pass: ``nixfmt`` for Nix and ``ruff``
for Python, at the line length in ``pyproject.toml``. It only formats; to
*verify* formatting use ``nix flake check`` (the ``formatting`` check), never
``nix fmt --check``, which is not a flag.

.. code-block:: console
   :class: cmd-host

   $ nix fmt                  # format Nix and Python in place
   $ nix run .#format         # ruff lint-fix (import order) and format Python
   $ nix run .#reflow         # rewrap wmill description fields to clean blocks

Type-checking
=============

``pyright`` runs from the checks shell. It is advisory: it is not part of the
gate, because a Windmill step's ``main()`` annotations are the UI form schema
rather than ordinary typing. ``nix run .#check`` lists the errors a commit adds
over the base, each under the commit that added its line, without failing on
them.

.. code-block:: console
   :class: cmd-host

   $ nix develop .#checks --command pyright

Documentation
=============

.. code-block:: console
   :class: cmd-host

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
   :class: cmd-host

   $ nix store gc                 # delete unrooted store paths
   $ rm result && nix store gc    # drop a build, then reclaim it

Other
=====

.. code-block:: console
   :class: cmd-host

   $ nix run .#maintainers -- f/fstests/report.py   # who to Cc for a change
