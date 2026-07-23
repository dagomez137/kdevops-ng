.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

========================
Initialize the workbench
========================

The :src:`f/workbench/init` flow is the one-time bootstrap, run from the
UI after Windmill is up, and idempotent: re-run it any time to refresh
what it provisioned. It sets up the three durable pieces of the build
area (the Workbench, :doc:`/concepts/terms`) that every other flow
assumes:

1. ``fetch``: the shared sources. Each picked project gets a durable
   bare repository (default ``$SYSTEM_DIR/bare/linux.git`` and
   ``$SYSTEM_DIR/bare/qemu.git``) that every worker cuts cheap detached
   worktrees off (:doc:`kernel-build`, :doc:`qemu-build`). A bare
   tracks its real upstream but borrows objects from the local mirror
   under ``$MIRRORS_DIR``, and keeps ``refs/heads/*`` for developer
   pushes.
2. ``ssh_key``: the kdevops-managed VM SSH key, one keypair under
   ``$SYSTEM_DIR/ssh/`` that every guest built afterwards trusts. The
   step returns the one ``Include`` line to add to :cmd:`~/.ssh/config`
   once, after which a plain ``ssh <vm>`` reaches any guest
   (:doc:`guests`).
3. ``mirror``: the mirror refresh timers, a ``git-mirror@<repo>.service``
   and ``.timer`` pair per mirror under ``$SYSTEM_DIR/mirror``,
   force-refreshing each from its upstream on a self-pacing loop.

The run form
============

**Projects** is the curated checklist of what to mirror, each its own
merged bare under ``$MIRRORS_DIR``; selecting a project reveals its
deploy options (the **Linux** and **QEMU** groups control how each
mirror is deployed and refreshed). **Peers** adds other workbench hosts
as remotes on each bare, for cross-host development branches
(:doc:`/concepts/cross-host-development`). **Refresh** (default on)
fetches new refs and tags on already-present bares.

**Setup SSH** (default on) provisions the VM key and the includable
ssh-config; off skips it and an existing key is left alone.
**Regenerate SSH Key** forces a fresh keypair even when one exists,
which invalidates the authorized key already baked into every deployed
guest, so only use it when rotating deliberately. **Setup Mirrors**
installs and enables the ``git-mirror@<repo>`` refresh timers.

Verifying the result
====================

The flow's result carries what each step provisioned, including the
``Include`` line for ``~/.ssh/config``. The timers are ordinary
``systemd --user`` state on the host:

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ systemctl --user list-timers 'git-mirror@*'
   $ journalctl --user-unit=git-mirror@linux.service
