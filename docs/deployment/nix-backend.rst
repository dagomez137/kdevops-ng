.. SPDX-License-Identifier: copyleft-next-0.3.1

===========
Nix backend
===========

The Nix backend (``deploy/nix/``) builds a custom Windmill server from source
with Nix and runs the whole stack under ``systemd --user``, with no container
runtime. A Nix-built binary links ``/nix/store`` and runs natively on the host,
so the thing that ruled Nix out for the container image is the natural fit here.

Build and deploy run from the repository root as two flake apps:
``nix run .#windmill-build`` and ``nix run .#windmill-deploy``. There is no
install script; the apps run the steps shown below, so you can equally build a
single component or install by hand. Everything else is sane defaults plus the
ordinary ``systemd`` override mechanisms.

Build
=====

``nix run .#windmill-build`` builds every component to a GC-rooted out-link
under the user state directory. The out-link is a stable path that always points
at the current build and survives ``nix store gc``; the units reach the binary
through it with the ``%S`` (state directory) specifier, because ``systemd``
expands specifiers in the executable path but not environment variables. It
runs:

.. code-block:: shell

   sw=~/.local/state/windmill/sw
   nix build ./deploy/nix#windmill       --out-link "$sw/windmill"
   nix build ./deploy/nix#postgresql     --out-link "$sw/postgresql"
   nix build ./deploy/nix#db-setup       --out-link "$sw/db-setup"
   nix build ./deploy/nix#caddy          --out-link "$sw/caddy"
   nix build ./deploy/nix#windmill-extra --out-link "$sw/windmill-extra"

The server build is heavy (around 10 GB and a clean compile of about 18
minutes). ``./deploy/nix#windmill-oracle`` is the same server with the
fourteenth language, Oracle, which pulls the unfree Oracle Instant Client.

Deploy
======

``nix run .#windmill-deploy`` does the whole sequence at once: build, install,
activate. The two deploy stages also run on their own, so you can customise the
installed units (``systemctl --user edit``) between installing and activating.

Install
-------

``nix run .#windmill-install`` places the units in the user unit directory and
the Caddyfile where the proxy reads it:

.. code-block:: shell

   cp deploy/nix/systemd/*.service ~/.config/systemd/user/
   cp deploy/nix/Caddyfile ~/.local/state/windmill/Caddyfile

Activate
--------

``nix run .#windmill-activate`` reloads the manager onto the installed units,
lingers the user so the services run without an active login session, then
enables and starts them. ``enable --now`` enables (creates the ``[Install]``
symlinks so they start at login) and starts in one step:

.. code-block:: shell

   systemctl --user daemon-reload
   loginctl enable-linger "$USER"
   systemctl --user enable --now \
       windmill-db windmill windmill-extra windmill-native windmill-caddy
   systemctl --user enable --now windmill-worker@0 windmill-worker@1

The database service runs ``windmill-db-setup`` on first boot: it initialises
the cluster under the state directory, rotates the role password off the shared
default to a generated secret, creates the ``windmill`` database, and writes the
``DATABASE_URL`` the rest read. The server then listens on ``127.0.0.1:8002``,
the LSP gateway on ``127.0.0.1:3001``, and caddy fronts both on
``127.0.0.1:8000``. Reach the UI over an SSH forward:

.. code-block:: shell

   ssh -L 8000:localhost:8000 <user>@<host>   # then https://localhost:8000

The default is HTTPS with caddy's internal CA, so the browser warns once on the
untrusted certificate. Run ``<state>/sw/caddy/bin/caddy trust`` to install the
root and remove the warning.

Configure
=========

Each unit ships sane defaults as ``Environment=`` lines and reads an optional
``%E/windmill/<unit>.env`` override file (``%E`` is ``$XDG_CONFIG_HOME``).
Override either by editing that file or with a drop-in. ``systemctl edit`` opens
``$SYSTEMD_EDITOR``, then ``$EDITOR``, then ``$VISUAL``, falling back to a
built-in default, so set one to use your editor:

.. code-block:: shell

   SYSTEMD_EDITOR=hx systemctl --user edit windmill.service

Export ``SYSTEMD_EDITOR`` from your shell profile to make it the default.

The knobs:

* The server port (``PORT``, default ``8002``) and base URL (``BASE_URL``,
  default ``https://localhost:8000``) live on ``windmill.service``.
* The public port (``WINDMILL_CADDY_PORT``, default ``8000``) lives on
  ``windmill-caddy.service``; the Caddyfile reads it.
* Workers are ``windmill-worker@`` instances; ``WORKER_GROUP`` and
  ``WORKER_TAGS`` select the jobs each pulls. See `Workers`_ for the build pool
  and the vm workers the kdevops workspace needs.
* The build-area paths (``WORKBENCH_DIR`` and friends) live on the worker units;
  see `The workbench`_ below.

Workers
-------

Workers are ``windmill-worker@`` instances differentiated only by worker group
and tags, the canonical Windmill mechanism. The default group ``default`` is the
build pool, so enabling more instances widens build concurrency.

The kdevops workspace also drives QEMU virtual machines through systemd (the
``f/qsu`` steps), which a default worker does not serve. Those jobs use the
``vm`` group, split across two tags so a long job never starves a quick one: the
``vm`` tag is the quick lifecycle and control ops (boot, stop, destroy,
status), the ``vm-run`` tag is only the long-lived fstests wait poll. The
``vm-run`` instance count is the concurrent-test-run cap.

Give an instance the vm role with a per-instance drop-in (``systemctl edit``
opens your editor, set above):

.. code-block:: shell

   systemctl --user edit windmill-worker@2

.. code-block:: ini

   [Service]
   Environment=WORKER_GROUP=vm
   Environment=WORKER_TAGS=vm

Use ``WORKER_TAGS=vm-run`` on the instances that run the poll, then enable each
with ``systemctl --user enable --now windmill-worker@2``. The vm group needs the
:term:`System workbench` provisioned and the host ``vhost_vsock`` module loaded.

On a separate host
~~~~~~~~~~~~~~~~~~~

A second machine can run workers for an existing server without the rest of the
stack, in two steps with the database pointed at the server in between.
``nix run .#windmill-worker-install`` builds only the worker binary and installs
only the ``windmill-worker@`` unit, with a drop-in that clears its
local-database dependency and makes the server-written ``database.env``
optional. It bakes in no ``DATABASE_URL``, because a worker-only host has no
local database to default to; set it, then enable as many instances as you want:

.. code-block:: shell

   nix run .#windmill-worker-install
   systemctl --user edit windmill-worker@
   nix run .#windmill-worker-activate -- 4

In the editor ``systemctl edit`` opens, set the server's database for every
instance:

.. code-block:: ini

   [Service]
   Environment=DATABASE_URL=postgres://user:pw@server:5432/windmill

``windmill-worker@`` is a systemd template, so each instance points at the one
unit file. Enabling more workers afterwards needs no rebuild or reinstall, just
``systemctl --user enable --now windmill-worker@4``. The server's PostgreSQL
must be reachable from this host: it binds ``127.0.0.1`` by default, so expose
it or tunnel. Build-pool workers also need the :term:`System workbench`
provisioned here, the same ``f/workbench`` init flow as on any worker host.

TLS and the base URL
--------------------

The ``Secure`` flag on Windmill's session cookie follows the server
``BASE_URL``: the server sets it from a base URL that starts with ``https://``,
not from a forwarded-proto header. So ``BASE_URL`` and the scheme caddy serves
must agree. Serving HTTPS with an ``http://`` base URL leaves the cookie
non-Secure; the reverse drops the cookie and breaks login. The defaults pair
(HTTPS plus ``https://localhost:8000``); change both together. To serve plain
HTTP, or an operator certificate instead of the internal CA, edit the Caddyfile
as its header comment describes and set ``BASE_URL`` to match.

The workbench
-------------

The worker build-area paths point at a :term:`Workbench`: a directory
containing the :term:`Developer`'s :term:`Worktree-groups <Worktree-group>` and
the kdevops-ng infrastructure that defaults under it. It is not a Windmill
workspace. The infrastructure is the :term:`System workbench` (``system/``,
``SYSTEM_DIR``), the host-local singleton holding the mirrors, bares, ssh key
and store, and the :term:`Worker sandboxes <Worker sandbox>` (``workers/<id>/``,
``WORKERS_DIR``), where each worker builds in its own worktree, never in a
developer's worktree.

The units default ``WORKBENCH_DIR`` to ``%S/windmill/workbench``, under the
systemd state directory (``%S`` is ``$XDG_STATE_HOME``), the recommended place
for persistent service state. Each piece relocates on its own: ``WORKBENCH_DIR``
moves the whole area, the worktree-groups included, so set it to put the groups
where you want them, a directory in ``$HOME`` such as ``$HOME/src`` or one
nested in the repository such as ``kdevops-ng/workbench``; ``SYSTEM_DIR`` and
``WORKERS_DIR`` default inside it but move out independently. Override any of
them with a drop-in or the ``windmill-worker.env`` file.

Run the ``f/workbench`` init flow from Windmill to provision the System
workbench (the bare mirrors and the ssh key); the workers fill their sandboxes
as jobs run.

Tear down
=========

``nix run .#windmill-teardown`` does the reverse of deploy in one shot:
deactivate, uninstall, wipe. The stages also run on their own, so you can stop
the services without removing anything, or wipe the data but keep the units.

Deactivate
----------

``nix run .#windmill-deactivate`` stops and disables the services and any worker
instances. ``disable --now`` disables the ``[Install]`` symlinks and stops in
one step:

.. code-block:: shell

   systemctl --user disable --now 'windmill*'

Linger is left enabled. It is user-global, not a Windmill setting, so disabling
it would stop every lingering user service, the workbench mirrors included. Drop
it explicitly, only when nothing else needs it, with the ``disable-linger`` app
(``loginctl disable-linger "$USER"``).

Uninstall
---------

``nix run .#windmill-uninstall`` removes the installed units and the Caddyfile,
then reloads the manager:

.. code-block:: shell

   rm --force ~/.config/systemd/user/windmill*.service
   rm --force ~/.local/state/windmill/Caddyfile
   systemctl --user daemon-reload

Wipe
----

``nix run .#windmill-wipe`` deletes the instance data under the state directory:
the database cluster, the build out-links, and the generated env. It leaves the
build-area workbench (also under the state directory) alone. Run it after
deactivate so the cluster is stopped:

.. code-block:: shell

   state=~/.local/state/windmill
   rm --recursive --force "$state/pgdata" "$state/sw" "$state/env"

Switching from the podman backend
==================================

The units reuse the same names as the podman backend, and static user units
shadow podman's quadlet-generated ones, so the two cannot run at once. Retire
podman first by moving its quadlets aside
(``~/.config/containers/systemd/windmill*``) and reloading, then deploy this
backend. The workspace itself lives in git, so push it to the fresh instance
with ``wmill sync push`` once the stack is up.
