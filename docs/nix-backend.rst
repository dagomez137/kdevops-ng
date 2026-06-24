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

   sw=~/.local/state/windmill-nix/sw
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
   cp deploy/nix/Caddyfile ~/.local/state/windmill-nix/Caddyfile
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
``%E/windmill-nix/<unit>.env`` override file (``%E`` is ``$XDG_CONFIG_HOME``).
Override either by editing that file or with a drop-in:

.. code-block:: shell

   systemctl --user edit windmill.service      # writes a drop-in override.conf

The knobs:

* The server port (``PORT``, default ``8002``) and base URL (``BASE_URL``,
  default ``https://localhost:8000``) live on ``windmill.service``.
* The public port (``WMNIX_CADDY_PORT``, default ``8000``) lives on
  ``windmill-caddy.service``; the Caddyfile reads it.
* The build-pool size is the number of ``windmill-worker@`` instances you
  enable. The ``vm`` and ``vm-run`` pools
  (``windmill-worker-vm@``, ``windmill-worker-vmrun@``) are templates you enable
  once the workbench is provisioned.
* The build-area paths (``WORKBENCH_DIR`` and friends) default under the state
  directory on the worker units; point them at a real workbench to run builds.

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

Tear down
=========

.. code-block:: shell

   systemctl --user disable --now 'windmill*'
   rm ~/.config/systemd/user/windmill*.service
   systemctl --user daemon-reload

The state under ``~/.local/state/windmill-nix`` (the cluster, the secret, the
out-links) is left in place; remove it to wipe the database.

Switching from the podman backend
==================================

The units reuse the same names as the podman backend, and static user units
shadow podman's quadlet-generated ones, so the two cannot run at once. Retire
podman first by moving its quadlets aside
(``~/.config/containers/systemd/windmill*``) and reloading, then deploy this
backend. The workspace itself lives in git, so push it to the fresh instance
with ``wmill sync push`` once the stack is up.
