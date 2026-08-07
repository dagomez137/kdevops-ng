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
     - booting a VM (:src:`f/qsu`)
   * - devel
     - ``kernel-devel-<release>`` /
       ``qemu-devel-<version>-<label>-<identity>``
     - the build dir's ``.cmd`` command database, the generated headers and
       sources, and the kconfig files a Rust index run reads, or meson's
       ``compile_commands.json`` and the generated headers
     - the clangd and rust-analyzer indexes on a developer worktree

Keeping the layers apart means a boot fetch stays lean and never drags the much
larger devel layer (186 MiB for a ``CONFIG_RUST=y`` kernel, 24 MiB for QEMU),
while a developer fetching an index never pulls boot images. Each devel layer's
composition, and the allowlist that builds it, live in
:src:`f/kernel/publish_devel.py` and :src:`f/qemu/publish_devel.py`.

What a layer ships follows from what its build system leaves behind, and the
rule is the same for both: ship whatever a generator can be replayed against,
and relocate only where no generator exists.

kbuild leaves a generator for each index, so the kernel layer ships inputs and
replays both locally. For C it ships the ``.cmd`` command database and
:src:`fetch_devel <f/kernel/fetch_devel>` replays it with the kernel's own
``gen_compile_commands.py``. For Rust it ships the seven generated ``.rs``
sources plus ``include/generated/rustc_cfg`` and ``include/config/auto.conf``,
and the same step runs the ``rust-analyzer`` target of
``scripts/Makefile.build``. Both indexes therefore name the consuming worktree
by construction, with no path rewriting. ``rust-project.json`` could not be
relocated even if that were wanted: kbuild hands the generator
``$(realpath $(srctree))`` and ``$(realpath $(objtree))``, so every path in it
is absolute, and four of its crates root under a toolchain store path that no
substitution anchor reaches. The decision is recorded in
:src:`ADR 0013 <notes/adr/0013-rust-index-regenerated-on-the-consumer.md>`.

Meson leaves no database: it emits the finished ``compile_commands.json`` when
it configures, with the builder's absolute paths recorded in it. So the QEMU
layer ships that index and :src:`fetch_devel <f/qemu/fetch_devel>` relocates
it, substituting the builder's build dir and worktree for the consuming ones in
the index and in the layer's symlinks.

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

When a developer worktree is requested, the flow also sets ``devel`` and the
sweep repeats for the devel layer, skipped when this host already has it. Both
layers therefore land before ``reuse_check`` probes them. Without this, a peer's
run layer would arrive on its own, ``devel_present`` would be false, and the
rule below would rebuild the whole thing locally to regenerate a layer the peer
had already published. It stays off by default so a boot-oriented build never
drags the much larger devel layer across the wire.

publish and publish_devel
-------------------------

``publish`` adds the run layer after a real install. ``publish_devel`` adds the
devel layer, and skips on the **devel** layer's own presence rather than on the
run layer's, because the two are independent: an identity built before the devel
layer existed, or one whose run layer was fetched from a peer, has one and not
the other. Gating the devel publish on the run layer would be self-perpetuating,
since the run layer's presence is exactly what would keep the devel layer from
ever being published.

For the same reason ``reuse_check`` reports ``devel_present`` alongside
``present``, and a run with a developer worktree requested does not accept a
run-layer hit when the devel layer is missing: it rebuilds, publishes the devel
layer, and indexes the worktree. Install and publish stay skipped in that case,
since the run layer really is present. Once the devel layer exists, the fast
reuse path returns.

fetch_devel
-----------

``fetch_devel`` resolves the devel layer (locally or from a peer) and copies the
developer subset into a developer worktree's build dir, leaving indexes that
point at that worktree's own source. The C index is regenerated in the
``transfer`` devShell; the Rust index needs the pinned ``rustc``, so that half
runs in ``build-kernel`` while all cross-host I/O stays in ``transfer``. The
Rust half gates on its inputs being present rather than on an exit status,
because a run missing ``auto.conf`` or ``rustc_cfg`` still exits 0 and still
writes a plausible but wrong index. A layer published before those inputs
joined the allowlist, a kernel without ``CONFIG_RUST=y``, and a kernel too old
to carry the generator each print a reason and leave the Rust index unset,
never costing the developer the C index. ``reuse_check`` treats a devel layer
with no ``.config`` as absent, so a pre-existing layer is republished once
rather than indexing worktrees forever with no Rust half. It runs standalone,
and it also runs as
the tail of either build flow when **Deploy Developer Worktree** is on, after
:src:`f/workbench/worktree/init` has laid the group worktree at the built ref.
Pointing both build flows at one worktree-group is how that group comes to hold
both projects, ``linux`` and ``qemu``, each indexed against the build that
produced it.

Cross-host fetch
================

The build flows' run-layer auto-fetch is driven by the ``use_peers`` toggle in
the Reuse group and the peers registry at ``SYSTEM_DIR/peers`` (one
``<host> [<store-index>]`` per line, written by :src:`f/workbench/fetch`). With
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

The :src:`f/common/store_index` step reads and maintains the catalog:

* ``list`` (the default): the local catalog with sizes and validity, plus a
  peer's when ``remote`` and ``remote_index`` are set.
* ``inspect <name>``: one identity's store path, closure size, and validity.
* ``forget <name>`` (with ``confirm``): drop one entry's GC root so ``nix store
  gc`` can reclaim its store path. The build leaves the catalog but remains
  rebuildable.
* ``prune``: drop every entry whose store path was already collected (that is,
  every dangling symlink).

By hand
-------

The catalog is a directory of indirect GC roots, so the unified ``nix`` CLI
inspects and prunes it directly, with no extra tooling. The directory is
``SYSTEM_DIR/store-index``; on the default layout that is
``~/.local/state/windmill/workbench/system/store-index`` (the worker's
``STORE_INDEX_DIR`` is not set in your own shell):

.. code-block:: console
   :caption: host
   :class: cmd-host

   $ idx=~/.local/state/windmill/workbench/system/store-index

List every cached build by size, largest first. ``nix path-info`` resolves each
catalog symlink to its store path for you:

.. code-block:: console
   :class: cmd-host

   $ nix path-info --closure-size --human-readable "$idx"/* \
       | sort --human-numeric-sort --key=2 --reverse

Inspect one build: its size, the files it installs, and a signature and content
integrity check. ``nix store ls`` needs a store path rather than the GC-root
symlink, so resolve the entry once with ``readlink`` (substitute a real name
from the list above):

.. code-block:: console
   :class: cmd-host

   $ sp=$(readlink --canonicalize "$idx"/kernel-7.1.0-vanilla-<hash>)
   $ nix path-info --closure-size --human-readable "$sp"
   $ nix store ls --long --recursive "$sp"
   $ nix store verify --recursive "$sp"

A published run layer is added with ``nix store add-path``, which records no
references, so an entry's closure is just itself: ``--recursive`` and ``nix
why-depends`` have nothing to walk here. To compare two builds, ``nix store
diff-closures`` still reports their release names and the size delta, a quick
"are these different, and by how much":

.. code-block:: console
   :class: cmd-host

   $ nix store diff-closures \
       "$idx"/kernel-7.1.0-vanilla-<hashA> \
       "$idx"/kernel-7.1.0-iomap-v3-<hashB>

Read the whole catalog as JSON for scripting:

.. code-block:: console
   :class: cmd-host

   $ nix path-info --json --closure-size "$idx"/* | jq

Reclaim space. Forgetting a build only removes its catalog symlink (its GC
root); the store path itself survives until the next collection, which is why
``forget`` stays reversible until you collect. Find dangling entries and
preview a collection before running it:

.. code-block:: console
   :class: cmd-host

   $ find -L "$idx" -maxdepth 1 -type l    # dangling: the store path is gone
   $ rm "$idx"/kernel-7.1.0-vanilla-<hash> # forget one build
   $ nix store gc --dry-run                # preview what a collection frees
   $ nix store gc                          # collect for real

A peer's catalog is the same directory read over ssh:

.. code-block:: console
   :class: cmd-host

   $ ssh <host> ls "$idx"

.. _Linux kernel: https://www.kernel.org/
.. _Nix: https://nixos.org/
.. _Nix store: https://nix.dev/manual/nix/2.24/store/
.. _QEMU: https://www.qemu.org/
