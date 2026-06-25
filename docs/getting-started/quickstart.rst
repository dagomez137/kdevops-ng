.. SPDX-License-Identifier: copyleft-next-0.3.1

===========
Quick start
===========

Deploy the Windmill instance and push the workspace, all from Nix. Install the
prerequisites first (see :doc:`requirements`). Each step links to its full
reference.

Deploy
======

Build and run the whole stack under ``systemd --user``:

.. code-block:: console

   $ nix run .#windmill-deploy                  # build, install, activate
   $ ssh -L 8000:localhost:8000 <user>@<host>   # only if the host is remote

Open ``https://localhost:8000`` in a browser on the host. The browser warns once
on caddy's internal certificate; trust it with ``nix run .#windmill-trust``. See
:doc:`/deployment/nix` for what is built, configuration, workers, and teardown.

Run wmill
=========

``wmill`` is provisioned from Nix, so run it from the dev shell rather than a
host install:

.. code-block:: console

   $ nix develop --command wmill --version     # wmill from Nix
   $ nix develop --command wmill sync push     # files -> instance

See :doc:`wmill` for connecting the CLI, the pull and push workflows, and
previewing a flow.
