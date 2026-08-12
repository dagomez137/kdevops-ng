.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

=======
Upgrade
=======

The instance runs a custom `Windmill`_ build from the project's fork, pinned
by revision and hash in :src:`deploy/nix/windmill/package.nix`. An upgrade
rebases the fork's ``integration/fixes`` branch onto the new upstream
release, bumps the pin, backs up the database, and restarts the stack in
order. The example commands are the 1.741.0 to 1.785.0 upgrade; substitute
the versions at hand. This page assumes the running stack of
:doc:`/deployment/nix`.

Back up the branches
====================

In the fork checkout, keep the current state reachable before rewriting
anything (``local`` here is a bare mirror remote). The database is backed up
separately, right before the roll.

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ git branch backup/2026-08-11/integration/fixes integration/fixes
   $ git push local backup/2026-08-11/integration/fixes

Repeat for any other branch worth keeping. Reverting the code side is
resetting to the backup branch.

Rebase the fork
===============

Fetch the new release tag from upstream and move the fix branch onto it,
then push it to the GitHub fork the pin fetches from:

.. code-block:: console
   :class: cmd-host

   $ git fetch origin --tags
   $ git rebase --onto v1.785.0 v1.784.0 integration/fixes
   $ git log --oneline v1.785.0..integration/fixes
   $ git push <fork-remote> integration/fixes

The log must list exactly the carried fixes. Drop any commit the new
release already contains.

Bump the pin
============

In :src:`deploy/nix/windmill/package.nix`, set ``version`` and ``rev``, and
replace the source ``hash`` with the prefetched one:

.. code-block:: console
   :class: cmd-host

   $ nix flake prefetch github:dagomez137/windmill/<rev> --json

Never pair a new ``rev`` with the old ``hash``: a fixed-output fetch is
identified by its hash alone, so Nix would silently reuse the old sources.

The vendored dependencies moved too. Set ``cargoHash`` and ``npmDepsHash``
to the fake ``sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=`` and let
one build report both real values, then fill them in:

.. code-block:: console
   :class: cmd-host

   $ cd deploy/nix
   $ nix build .#windmill --keep-going --no-link
   error: hash mismatch in fixed-output derivation '...-vendor.drv':
            specified: sha256-AAAA...
               got:    sha256-vvFtT/Dwb1bvzLo77d6fJ0lgZus4DmxOdlWqothfRvc=

Build
=====

Build to a staging out-link first. It GC-roots the new build without
touching the link the units run, so a crash or restart cannot start the new
server, and its one-way database migrations, before the backup exists. A
clean compile takes about 18 minutes; success also proves the ``postPatch``
pins still match the new sources, since every substitution fails loudly on
drift.

.. code-block:: console
   :class: cmd-host

   $ pkgs=~/.local/state/windmill/pkgs
   $ nix build .#windmill --out-link "$pkgs/windmill-next"

Back up the database
====================

The new server applies schema migrations on first start and they are
one-way, so this dump is the database's only revert path. Check the Runs
page shows nothing executing, then dump with :cmd:`pg_dump` from the
stack's own ``postgresql`` out-link, so client and server versions agree,
and verify the archive lists:

.. code-block:: console
   :class: cmd-host

   $ set -o allexport; source ~/.local/state/windmill/env/database.env
   $ set +o allexport
   $ dump=~/.local/state/windmill/backups/windmill-pre-1.785.0.dump
   $ mkdir --parents "${dump%/*}"
   $ "$pkgs/postgresql/bin/pg_dump" --dbname="$DATABASE_URL" \
         --format=custom --file="$dump"
   $ "$pkgs/postgresql/bin/pg_restore" --list "$dump" | wc --lines

Roll
====

Swap the link and restart the server first, so it runs the migrations
before any worker of the new version pulls a job; the build is already
done, so the swap is instant. Then roll the workers and drop the staging
root:

.. code-block:: console
   :class: cmd-host

   $ nix build .#windmill --out-link "$pkgs/windmill"
   $ systemctl --user restart windmill.service
   $ journalctl --user --unit windmill.service --since "5 min ago"
   $ systemctl --user restart windmill-native.service 'windmill-worker@*'
   $ rm "$pkgs/windmill-next"

The journal shows each migration apply. Instance settings and worker groups
live in the database and the unit drop-ins, so they carry over untouched.
``windmill-extra`` is an independent package; rebuild it separately only
when the LSP gateway should track the same sources.

Verify
======

.. code-block:: console
   :class: cmd-host

   $ readlink --canonicalize "$pkgs/windmill"
   /nix/store/...-windmill-1.785.0

The Workers page must show every worker on the new version. A run form with
a dynamic dropdown is a good end-to-end probe, since its helper jobs
exercise a worker, the queue and the frontend at once. Commit the pin bump
on its own, gates first, as in :doc:`/contributing/development`.

Revert
======

Stop the server and workers (the database service stays up), restore the
dump, and reset the pin:

.. code-block:: console
   :class: cmd-host

   $ systemctl --user stop windmill.service windmill-native.service \
         'windmill-worker@*'
   $ "$pkgs/postgresql/bin/pg_restore" --dbname="$DATABASE_URL" \
         --clean --if-exists "$dump"
   $ git revert <the pin commit>   # or reset the fork to its backup branch

Rebuild and restart as above. The old build is usually still in the Nix
store, so the rebuild is a link swap.

.. _Windmill: https://www.windmill.dev/
