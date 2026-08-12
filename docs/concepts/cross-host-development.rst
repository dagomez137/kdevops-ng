.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

======================
Cross-host development
======================

A developer on one host can hand a branch to a worker on another host to
build: develop on the thin box, build on the beefy one. Build *outputs*
already cross hosts through the :doc:`/concepts/build-store`; this page covers
the *input* side, the per-host ref channel that lets a development branch
travel from one host to another over SSH (see ADR 0001, bare is the working
repo).

The model
=========

Same-host, a developer and a worker share one Bare, so publishing a branch is
just a commit and the worker builds it. Cross-host, the developer pushes the
branch to the peer's Bare over SSH, and the peer's worker builds it from its
own ``refs/heads/*``. No build-flow change is needed, because the worker's
``prepare()`` already resolves a literal ref against the local Bare.

Refs (build inputs) cross by ``git``; Store entries (build outputs) cross by
``nix copy``. The two directions are independent: the peer remotes described
here have no bearing on the Mirror or upstream remotes, nor on the Store
catalog.

Peer remotes
============

A peer is named by its SSH-host alias, the same alias the developer would type
to :cmd:`ssh` into the other host. Provisioning registers each alias as a remote
on every Bare on this host, with the remote's URL derived from the shared
:term:`System workbench` layout:

::

   ssh://<peer>/<SYSTEM_DIR>/bare/<project>.git

and the refspec:

::

   +refs/heads/*:refs/remotes/<peer>/*

The URL derivation assumes peers share this host's ``SYSTEM_DIR`` path, which
holds when the hosts share a home directory (for example one NFS-exported
``/home``). The refspec maps the peer's heads into a private
``refs/remotes/<peer>/*`` namespace, so a fetch from the peer never collides
with local branches.

Provisioning only wires the remote; it does not fetch. Push is the workflow,
and a peer may be empty or unreachable at provisioning time. If the remote
already exists its URL is refreshed in place, so re-running is idempotent. List
the *other* hosts as peers, never the host itself.

SSH prerequisite
----------------

A peer alias resolves through :cmd:`~/.ssh/config`, and the transfer uses the
same passwordless SSH the Store uses (the ``transfer`` devShell's OpenSSH). Keep
``~/.ssh/config`` at mode ``0600``.

Provisioning
============

Run :src:`f/workbench/init` (which drives :src:`f/workbench/fetch`) with a
``peers`` list of the SSH-host aliases of the other hosts:

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ wmill flow run f/workbench/init --data '{"peers": ["hetzie"]}'

The peer wiring is implemented by the ``_ensure_peers`` helper in
:src:`f/workbench/fetch.py`: it adds (or refreshes) one ``<peer>`` remote per
alias on each Bare and sets the
``+refs/heads/*:refs/remotes/<peer>/*`` refspec, printing each remote it
touches.

Workflow
========

The cross-host loop has three steps, given hosts A and B.

Provision peers on both hosts
-----------------------------

Provisioning is symmetric: on host A pass ``peers: ["B"]``, and on host B pass
``peers: ["A"]``. Each Bare now carries a remote for the other host.

Publish a branch
----------------

Push from a developer worktree on A. The worktree shares A's Bare, so it
inherits the ``<peer>`` remote:

.. code-block:: console
   :class: cmd-host

   $ git -C <worktree> push B HEAD:refs/heads/<branch>

The branch lands in B's Bare as ``refs/heads/<branch>``.

Build it on the peer
--------------------

Run B's build flow with the branch as the ref:

.. code-block:: console
   :class: cmd-host

   $ wmill flow run f/kernel/build --data '{"worktree":{"git_ref":"<branch>"}}'

B's worker ``prepare()`` resolves ``<branch>`` locally, because it is now a
``refs/heads/*`` entry in B's Bare and needs no fetch. It lays B's warm worker
worktree under the fixed ``main`` group and builds. The build's run layer can
then travel back to A through the :doc:`/concepts/build-store`, closing the
build-on-B, boot-on-A loop.

Fetch direction
===============

The ``+refs/heads/*:refs/remotes/<peer>/*`` refspec also lets a host fetch a
peer's development branches in the read direction:

.. code-block:: console
   :class: cmd-host

   $ git -C <bare> fetch <peer>

This is the symmetric counterpart to the push above. Push is the default
developer flow; fetch is available when a host needs to pull a peer's branches
into its own ``refs/remotes/<peer>/*`` namespace.

From a machine that is not a peer
=================================

Peer remotes are the provisioned, symmetric case, and they exist so the *other*
host's workers can build a branch. Reading work back out needs none of that. The
Bare is an ordinary bare repository served over SSH, so any machine that can
:cmd:`ssh` to this host can add it as a remote and fetch from it, with nothing
provisioned on either side and no workbench, no ``SYSTEM_DIR`` and no peers
entry on the client. A laptop with a plain checkout is enough:

.. code-block:: console
   :class: cmd-host

   $ git remote add <name> ssh://<user>@<host>//<SYSTEM_DIR>/bare/<project>.git
   $ git fetch <name>
    * [new branch]      ubsan-test -> <name>/ubsan-test

Note the doubled slash after the host. An :cmd:`ssh` URL with one slash names a
path relative to the login home, so an absolute path needs the second one. Both
forms work; they simply address different places.

What the client sees is the Bare's ``refs/heads/*``, which is exactly the
durable development-branch namespace: the branch a worktree published by
committing is fetchable from another machine the moment it exists, with no push
and no extra copy. The client picks its own remote name and refspec, since the
``refs/remotes/<peer>/*`` convention above is a provisioning choice on a
workbench host, not a property of the Bare.

Use :cmd:`git remote` this way when a machine wants to *read* the work. Use a
peer remote when that machine's workers must *build* it, because a worker
resolves a local ``refs/heads/*`` ref and a fetched
``refs/remotes/<peer>/*`` entry is not one.

The durable Bare
================

These peer remotes live on the Bare, the durable working repo that holds
development branches and worktrees (see :term:`Bare`). The Bare is the ref
channel between a developer and a worker, and the peer remotes extend that
channel across hosts: the same per-host durable repository that anchors
same-host collaboration becomes the cross-host endpoint, reached over SSH at a
predictable ``ssh://<peer>/<SYSTEM_DIR>/bare/<project>.git`` address.
