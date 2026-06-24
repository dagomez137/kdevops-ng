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
* Every worker is one ``windmill-worker@`` instance, and its ``WORKER_GROUP``
  and ``WORKER_TAGS`` select which jobs it pulls. The default is the build pool
  (group ``default``), so the build-pool size is just the number of instances
  you enable. For a specialised worker, override those two on the instance, for
  example ``WORKER_GROUP=vm`` with ``WORKER_TAGS=vm`` for the QEMU VM lifecycle
  ops or ``WORKER_TAGS=vm-run`` for the long fstests poll (the unit header
  explains the split and its workbench and ``vhost_vsock`` requirements).
* The build-area paths (``WORKBENCH_DIR`` and friends) live on the worker units;
  see `The workbench`_ below.

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

The workbench is the build area the workers use: the System workbench
(``SYSTEM_DIR``) holds the durable git mirrors and the ssh key for reaching
guests, and each worker gets a sandbox under ``WORKERS_DIR``. The worker units
default ``WORKBENCH_DIR`` to ``%S/windmill/workbench`` (under the state
directory).

Put it wherever suits you, a directory in ``$HOME`` such as ``~/workbench`` or
one nested in the repository such as ``kdevops-ng/workbench``, and point
``WORKBENCH_DIR`` (and ``SYSTEM_DIR`` or ``WORKERS_DIR`` if you relocate them
out of it) there with a drop-in or the ``windmill-worker.env`` file. Create the
directory, then run the ``f/workbench`` init flow from Windmill to provision the
durable bits, the System bare mirrors and the ssh key; the workers fill the
sandboxes as jobs run.

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
