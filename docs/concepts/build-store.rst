.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

===============
The build Store
===============

The Store lets an identical `Linux kernel`_ or `QEMU`_ build be reused or
fetched instead of rebuilt, whether on a single host or across a fleet. Every
build is keyed by a reproducible build identity. That identity is published to
the `Nix store`_ and indexed, so a later build with the same identity skips
compilation, and a peer's build can be pulled over the network. Each build
follows one decision rule: reuse a local build, else fetch a peer's, else build
from source. Fetch beats build.

The Store moves build outputs through the Nix store rather than ``rsync``. ADR
0005 first chose a custom identity-keyed destdir with an ``rsync`` fetch and
recorded the Nix-store transport (``nix store add-path`` plus ``nix copy``) as
the expected evolution; that evolution is what runs today. The toolchain is
already a pinned `Nix`_ devShell, so two hosts building from one ``flake.lock``
get a byte-identical toolchain closure, and publishing the outputs to that same
store and moving them with ``nix copy`` reuses the Nix machinery rather than
reinventing a transport.

Build identity
==============

The build identity is a short content hash over the inputs that fix a build's
bytes: the ``.config`` (minus its localversion), the ``build-kernel`` or
``build-qemu`` devShell derivation path (the toolchain), the make flags (with
host paths normalized), and the source tree (the worktree's ``HEAD`` tree
object, so a ``b4`` series re-applied with ``git am`` keeps one identity over
identical content even though each commit's SHA changes). The same identity
implies the same bytes. See ADR 0002 (build-identity-in-kernelrelease).

Where it can, a project bakes the identity into its own artifact so the result
self-reports it:

* The kernel bakes the identity into ``CONFIG_LOCALVERSION``, so ``uname -r``
  reports it directly as ``<version>-<label>-<digest>``, for example
  ``7.1.0-vanilla-<hash>``. The same identity yields one release name.
* QEMU has no release string, so the identity instead keys the install prefix,
  ``destdir/<version>-<label>-<identity>``.

Two layers per identity
=======================

A build publishes up to two independent store paths, kept separate so that each
consumer fetches only what it needs.

.. list-table::
   :header-rows: 1

   * - Layer
     - Name
     - Contents
     - Consumer
   * - run
     - ``kernel-<release>`` / ``qemu-<version>-<label>-<identity>``
     - boot image plus ``lib/modules/<release>``, or the QEMU install tree
     - booting a VM (``f/qsu``)
   * - devel
     - ``kernel-devel-<release>``
     - the build dir's ``.cmd`` command database and generated headers
     - the clangd or LSP index on a worktree

Keeping the layers apart means a boot fetch stays lean and never drags the much
larger devel layer (roughly 190 MB), while a developer fetching an index never
pulls boot images. The devel layer's composition, and the allowlist that builds
it, live in ``f/kernel/publish_devel.py``.

The catalog
===========

Every published identity is recorded as a symlink under the Store index at
``SYSTEM_DIR/store-index/``::

   kernel-7.1.0-vanilla-b9e826508b1e        -> /nix/store/<hash>-<name>
   kernel-devel-7.1.0-vanilla-b9e826508b1e  -> /nix/store/<hash>-<name>
   qemu-11.0.0-vanilla-3f2a1c8e9d04         -> /nix/store/<hash>-<name>

Each symlink is also a Nix GC root, created with ``nix build --out-link``, so
the store path survives ``nix store gc`` until the entry is removed. The catalog
is the authoritative, host-local list of available identities. Store-path names
alone are too noisy to trust, since nixpkgs ships its own ``-kernel-*`` paths. A
peer's catalog is simply the same directory read over SSH.

How the build flows use it
==========================

The kernel and QEMU build flows wire together a small set of Store steps. Most
are skipped on reuse, so they run only after a real build, except where noted.

reuse_check
-----------

``reuse_check`` runs before the compile and reports whether the identity is
already available. It checks the local destdir or prefix first, then the Store
catalog, where a fetched build lives. Configure has already run to bake the
identity; when it is present the compile, install, and publish steps are skipped
and the manifest points at the existing artifacts. It is store-aware, so a
fetched identity is consumed in place from ``/nix/store`` with no local copy.

fetch_identity
--------------

``fetch_identity`` runs before ``reuse_check``. With ``use_peers`` on it sweeps
the registered peers (the ``SYSTEM_DIR/peers`` registry) and, for the first that
already published this identity, reads its catalog entry over SSH, pulls the
store path with ``nix copy``, and indexes it locally, leaving the run layer in
the store for ``reuse_check`` to resolve.

publish and publish_devel
-------------------------

``publish`` and ``publish_devel`` run after a real install. They add the run
layer and the devel layer, respectively, to the Nix store and the catalog.

fetch_devel
-----------

``fetch_devel`` is a standalone developer step. It resolves
``kernel-devel-<release>`` (locally or from a peer), copies the developer subset
into the worktree's build dir, and regenerates ``compile_commands.json`` locally
so the index points at that worktree's own source.

Cross-host fetch
================

The build flows' run-layer auto-fetch is driven by the ``use_peers`` toggle in
the Reuse group and the peers registry at ``SYSTEM_DIR/peers`` (one
``<host> [<store-index>]`` per line, written by `f/workbench/fetch`_). With
``use_peers`` on, ``fetch_identity`` sweeps the registered peers and, for the
first that already published this identity, learns the peer's store path from
``ssh <host> readlink <index>/<name>`` and pulls it with
``nix copy --from ssh://<host>``. Because the two hosts share one toolchain
closure, a transported QEMU binary runs with no missing dependencies.

The explicit ``remote``/``remote_index`` knobs are the manual path, used by the
standalone steps rather than the build flows: the ``fetch_devel`` step and the
``store_index`` inspector take an ssh host and that peer's ``store-index``
directory to target one named peer directly instead of sweeping the registry.
All cross-host I/O happens inside the ``transfer`` devShell (Nix plus OpenSSH);
nothing uses ``rsync``.

This moves build outputs across hosts. Build inputs, such as a developer's
branch, cross the other way by git; see :doc:`/concepts/cross-host-development`.

.. note::

   The ``transfer`` devShell's OpenSSH rejects a group-writable
   ``~/.ssh/config`` with "Bad owner or permissions"; keep it ``0600``.

Inspecting and pruning
======================

The ``f/common/store_index`` step reads and maintains the catalog:

* ``list`` (the default): the local catalog with sizes and validity, plus a
  peer's when ``remote`` and ``remote_index`` are set.
* ``inspect <name>``: one identity's store path, closure size, and validity.
* ``forget <name>`` (with ``confirm``): drop one entry's GC root so ``nix store
  gc`` can reclaim its store path. The build leaves the catalog but remains
  rebuildable.
* ``prune``: drop every entry whose store path was already collected (that is,
  every dangling symlink).

The same operations by hand are::

   ls -l "$STORE_INDEX_DIR"/
   nix path-info --closure-size --human-readable "$(readlink .../<name>)"
   rm "$STORE_INDEX_DIR"/<name> && nix store gc
   ssh <host> ls "$STORE_INDEX_DIR"/

.. _Linux kernel: https://www.kernel.org/
.. _Nix: https://nixos.org/
.. _Nix store: https://nix.dev/manual/nix/2.24/store/
.. _QEMU: https://www.qemu.org/
.. _f/workbench/fetch:
   https://github.com/dagomez137/kdevops-ng/tree/main/f/workbench/fetch.flow
