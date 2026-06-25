.. SPDX-License-Identifier: copyleft-next-0.3.1

===========
Nix backend
===========

The Nix backend (``deploy/nix/``) builds a custom Windmill server from source
with Nix and runs the whole stack under ``systemd --user``, with no container
runtime. A Nix-built binary links ``/nix/store`` and runs natively on the host,
so the thing that ruled Nix out for the container image is the natural fit here.

There is no install script. Deployment is three moves: build the components,
drop the units in, and enable them. Everything else is sane defaults plus the
ordinary ``systemd`` override mechanisms.

Build
=====

Build each component to a GC-rooted out-link under the user state directory. The
out-link is a stable path that always points at the current build and survives
``nix store gc``; the units reach the binary through it with the ``%S`` (state
directory) specifier, because ``systemd`` expands specifiers in the executable
path but not environment variables.

.. code-block:: shell

   sw=~/.local/state/windmill/sw
   nix build .#windmill       --out-link "$sw/windmill"
   nix build .#postgresql     --out-link "$sw/postgresql"
   nix build .#db-setup       --out-link "$sw/db-setup"
   nix build .#caddy          --out-link "$sw/caddy"
   nix build .#windmill-extra --out-link "$sw/windmill-extra"

The server build is heavy (around 10 GB and a clean compile of about 18
minutes). ``.#windmill-oracle`` is the same server with the fourteenth language,
Oracle, which pulls the unfree Oracle Instant Client.

Deploy
======

Copy the units into the user unit directory and the Caddyfile to where the proxy
reads it, then enable the services. ``enable-linger`` lets them run without an
active login session.

.. code-block:: shell

   cp deploy/nix/systemd/*.service ~/.config/systemd/user/
   cp deploy/nix/Caddyfile ~/.local/state/windmill/Caddyfile
   loginctl enable-linger "$USER"
   systemctl --user daemon-reload
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

.. code-block:: shell

   systemctl --user disable --now 'windmill*'
   rm ~/.config/systemd/user/windmill*.service
   systemctl --user daemon-reload

The state under ``~/.local/state/windmill`` (the cluster, the secret, the
out-links) is left in place; remove it to wipe the database.

Switching from the podman backend
==================================

The units reuse the same names as the podman backend, and static user units
shadow podman's quadlet-generated ones, so the two cannot run at once. Retire
podman first by moving its quadlets aside
(``~/.config/containers/systemd/windmill*``) and reloading, then deploy this
backend. The workspace itself lives in git, so push it to the fresh instance
with ``wmill sync push`` once the stack is up.
