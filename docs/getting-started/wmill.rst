.. SPDX-License-Identifier: copyleft-next-0.3.1

==================
Working with wmill
==================

``wmill`` is the Windmill command-line client. It moves workspace content
between the running instance and this repository, where git is the source of
truth. See the upstream `wmill CLI`_ documentation for the full command set.

.. _wmill CLI: https://www.windmill.dev/docs/advanced/cli

``wmill`` is provisioned from Nix and pinned to the server version, instead of
being installed on the host. Enter the development shell with ``nix develop`` to
put it on ``PATH``, or prefix a single command with ``nix develop --command``:

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ nix develop --command wmill --version

Connecting
==========

Point the CLI at the local instance once it is running (see
:doc:`/deployment/nix` to bring it up). The CLI and the API talk to the server
directly on ``127.0.0.1:8002`` (plain HTTP); ``127.0.0.1:8000`` is caddy's HTTPS
front for the browser, so a request there over ``http://`` fails. ``wmill`` logs
in through a browser or a pasted token, and a host with no desktop has neither,
so fetch one token from the seeded superadmin (``admin@windmill.dev`` /
``changeme`` on a fresh instance); ``wmill`` does the rest, ``--create`` making
the workspace:

.. code-block:: console
   :class: cmd-host

   $ url=http://localhost:8002
   $ token=$(curl --silent --request POST "$url/api/auth/login" \
       --header 'Content-Type: application/json' \
       --data '{"email":"admin@windmill.dev","password":"changeme"}')
   $ wmill workspace add kdevops kdevops "$url/" --token "$token" --create

The token is saved to ``~/.config/windmill/remotes.ndjson``, so later deploys
need no re-auth; run them through the ``deploy-kdevops`` app (`Deploying`_). A
fresh instance seeds only a ``starter`` workspace, so ``--create`` adds
``kdevops``. Change that default password after the first login, and mint a
durable token with ``wmill token create`` if you want one. ``wmill init`` writes
AI-assistant context and a few editor files that are git-ignored; it is
optional.

Deploying
=========

Deploy the whole ``f/`` tree to the ``kdevops`` workspace through the deploy
app:

.. code-block:: console
   :class: cmd-host

   $ nix run .#deploy-kdevops

Once the push succeeds it runs the preview smoke suite against the steps
just deployed (:doc:`/contributing/testing`); a ``--dry-run`` skips it.

Two workflows
=============

The project supports two ways to work, and a contributor may choose either.

Bidirectional (recommended)
---------------------------

Edit in the Windmill UI or on disk, then reconcile through git. Because the
files are kept in the canonical form described in
:doc:`/reference/wmill-yaml`, the round-trip has no diff:

.. code-block:: console
   :class: cmd-host

   $ wmill sync pull          # instance -> files
   $ nix run .#reflow         # keep descriptions as clean literal blocks
   $ nix run .#deploy-kdevops # files -> production
   $ git add <paths> && git commit --signoff

This workflow allows UI editing, ``wmill dev`` and ``wmill sync pull``.

Push-only
---------

Hand-author the YAML on disk, deploy with ``nix run .#deploy-kdevops``, and
never pull. This preserves hand-tuned formatting but does not capture UI edits
back to git. Prefer the bidirectional flow unless you have a reason not to.

Previewing a flow
=================

``wmill dev`` opens a live page that renders a local flow or script and writes
UI edits straight back to disk, one flow at a time, which is gentler than a bulk
pull:

.. code-block:: console
   :class: cmd-host

   $ wmill dev --path f/qsu/boot

See the `dev and preview`_ documentation for ``wmill dev`` and
``wmill flow preview``.

.. _dev and preview: https://www.windmill.dev/docs/advanced/cli/dev-preview

.. _running-a-flow:

Running a flow
==============

.. note::

   This view comes from the project's `Windmill fork`_; upstream ``wmill``
   prints the steps' output in delayed batches instead. We intend to upstream
   it.

.. TODO: link the upstream pull request once it is open.

.. _Windmill fork: https://github.com/dagomez137/windmill/tree/integration/fixes

:cmd:`wmill flow run` shows the run as it happens: a bar with one slot per step,
the step running now, the worker and host it runs on, and its latest output
line:

.. code-block:: console
   :class: cmd-host

   $ wmill flow run f/qsu/bringup --data @bringup.json
   f/qsu/bringup  01a1204d-d62c-9953-5af1-0c5362d218c7   7.9s   ✓ 3  ⊘ 0
   █│██▓··································│·····│·············│·····  3/61
   ▸ [4/61] build_kernel › configure › configure_preset   6.6s
     on wk-default-debian-xSOKD @ hz-debian   log: wmill job logs 01a1204d-db61…
   ┌ output ──────── [w] 10 lines  [l] follow  [q] detach
   │ + nix --extra-experimental-features 'nix-command flakes' develop 'path:/ho…

``w`` widens the output window to 10 lines (``+`` and ``-`` resize it), and
pressing it again hides the window. ``l`` streams all output, and ``q`` detaches
while the flow keeps running. Pass ``--follow`` to stream all output from the
start, or ``--no-view`` for plain output.

When the run ends, every step is listed with its job ID, under the subflow
that ran it. A step that ran also names its worker and host (left out below to
fit the page); a skipped step is dimmed:

.. code-block:: console
   :class: cmd-host

   █│█████▪▪▪▪▪▪██▪▪██▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪█│▪██▪█│████▪▪▪▪▪▪███│█████  61/61
     ✓ resolve                   0.0s  01a1204d-d639-ae8a-bde6-9a702418e561
     build_kernel                      01a1204d-d68e-3572-56e1-4db25c496b35
       ✓ prepare_worktree        0.8s  01a1204d-d6a3-6727-475d-d8628b041d3c
       ✓ build_flags             0.4s  01a1204d-d9b0-20c2-8e9a-d9c0de1fb40b
       configure                       01a1204d-db46-6e1a-7946-94cb6a7d983b
         ✓ configure_preset     12.2s  01a1204d-db61-25fd-839d-9d256dd3c4fa
       ✓ fetch_identity          0.1s  01a1204e-0b11-c0a9-904a-71ef43b04f73
       ✓ reuse_check             0.1s  01a1204e-0b70-5337-2193-1d62b89b9c4d
       ⊘ compile              skipped  01a1204e-0bd5-cac4-e458-13ced2273542
       ...
   Flow ran to completion: 26 ran, 35 skipped in 34.3s
   Logs: wmill job logs 01a1204d-d62c-9953-5af1-0c5362d218c7

Pass any of these IDs to ``wmill job logs`` to read one step, one subflow, or
the whole flow. The logs stay in Windmill and nothing is copied locally.

Generated content
=================

:src:`f/qsu/bringup.flow/flow.yaml` is generated by
``scripts/gen-bringup.py`` and is never pulled; regenerate it with
``python3 scripts/gen-bringup.py`` after changing a source subflow.
