.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

==========
Build QEMU
==========

The `f/qemu/build`_ flow builds a custom `QEMU`_ from source, reproducibly, for
the QEMU/systemd guest layer to consume. It is the Windmill equivalent of an
out-of-tree ``configure`` plus ``make`` of upstream QEMU at a pinned ref. The
flow deliberately mirrors `f/kernel/build`_: a mirror-backed git worktree built
inside the ``nixos-flake`` ``.#build`` devShell, producing a ``result.json``
manifest that a downstream flow reads. Most of this page is "do what the kernel
build does, for QEMU"; the differences are called out where they matter.

Why build QEMU at all
=====================

The guest layer needs a ``qemu-system-<arch>`` binary, and there are three
providers in increasing order of reproducibility:

.. list-table::
   :header-rows: 1
   :widths: 22 30 18 30

   * - Provider
     - Path
     - Reproducible
     - Use
   * - Host distro
     - ``/usr/bin/qemu-system-x86_64``
     - No
     - Never; we skip whatever the host ships.
   * - Custom build (this flow)
     - ``WORKERS_DIR/<slot>/qemu/destdir/bin/...``
     - Yes, given a pinned ref and the pinned Nix toolchain
     - A specific upstream ref, patch, or fork.
   * - Nix package (future)
     - ``/nix/store/.../bin/...``
     - Fully hermetic
     - The reproducible default once wired.

The whole point is to never depend on the host distro's QEMU. This flow is the
custom-version path; the future variant described below builds QEMU as a Nix
derivation into the store. Both emit the same manifest, so the guest layer does
not care which one produced the binary.

Because the build runs inside ``nix develop .#build``, which already carries
QEMU's full build toolchain (``inputsFrom = [ pkgs.qemu ]``: meson, ninja, GCC,
``pkg-config``, glib, pixman, and the rest), there is no distro-package install
step and no toolchain-presence check. Those stages, which a distro-driven build
needs, simply disappear here.

Provisioning
============

The flow reuses the durable Bare provisioning model that backs the kernel
build, substituting the project name (``qemu``). The chain has three layers.

A system workbench mirror ``$SYSTEM_DIR/mirror/qemu.git`` is a user-owned bare
mirror of ``qemu/qemu.git``, refreshed on a timer and sitting alongside the
kernel mirror ``$SYSTEM_DIR/mirror/linux.git``. It rides the system workbench
mount that every worker already has, so no separate mount is needed.

The ``f/workbench/init`` flow provisions a durable Bare at
``$SYSTEM_DIR/bare/qemu.git`` with ``git init --bare``, borrowing the mirror's
objects through an alternate and fetching its heads into
``refs/remotes/mirror/*``. ``refs/heads/*`` stays reserved for developer
branches. This step is
idempotent, run over `f/workbench/fetch`_ from a source list (kernel plus QEMU
by default).

Off that Bare, ``prepare_worktree`` re-syncs this worker's one warm ``main``
worktree at ``WORKERS_DIR/<WORKER_INDEX>/qemu`` to the requested ref with
``git worktree add --force --detach``. Re-syncing to the ref on every build
keeps rebuilds incremental, and because each worker has its own warm tree,
builds on different workers run in parallel. ``build/`` and ``destdir/`` are
children of that source checkout. Everything lives under ``WORKERS_DIR``, which
is bind-mounted at identical host paths, so a host-forked process (the guest's
QEMU) reads the artifacts directly. For the durable-Bare rationale see
:doc:`/concepts/build-store`.

The flow
========

The flow is a ``same_worker`` pipeline. It is structurally `f/kernel/build`_
without the config-method branch, because QEMU has a single configure path:

::

   prepare_worktree -> configure -> compile -> devtools -> install -> collect

.. list-table::
   :header-rows: 1
   :widths: 20 50 14

   * - Step
     - Action
     - Runs in
   * - ``prepare_worktree``
     - Sync this worker's warm ``main`` worktree of QEMU to ``ref`` off the
       Bare; create ``build/`` and ``destdir/``.
     - Host
   * - ``configure``
     - ``meson subprojects download`` in the source, then
       ``{src}/configure --target-list ... --prefix={destdir}`` with the
       chosen compiler and ``--disable-download``, run in ``build/``.
     - ``.#build``
   * - ``compile``
     - ``make -j$(nproc)`` in ``build/`` (drives ninja).
     - ``.#build``
   * - ``devtools``
     - Copy meson's ``compile_commands.json`` into the source root for
       ``clangd`` (on by default).
     - Host
   * - ``install``
     - ``make install`` in ``build/`` into ``destdir/`` (user-writable, no
       ``sudo``).
     - ``.#build``
   * - ``collect``
     - Write ``result.json`` and return it as the flow result.
     - Host

The warm-tree layout keeps the source at
``WORKERS_DIR/<WORKER_INDEX>/qemu`` with ``build/`` and ``destdir/`` as
children of it. ``--prefix={destdir}`` makes ``make install`` populate
``destdir/bin`` and ``destdir/share/qemu``; QEMU resolves its data directory
relative to that prefix, which is stable because the slot path is stable.

Schema inputs
=============

The form surfaces the choices a maintainer actually makes:

``qemu_ref``
   The tag, branch, or SHA to check out from the Bare. Default ``v11.0.0``,
   configurable exactly like the kernel flow's ``git_ref``.

``target_list``
   A multiselect of QEMU's emulator targets, passed as ``--target-list`` and
   enumerated from the source's ``configs/targets/*.mak`` (``*-softmmu`` for
   system emulation, ``*-linux-user`` and ``*-bsd-user`` for user mode).
   Default ``[x86_64-softmmu]``, comma-joined into one argv element.

``compiler``
   ``gcc`` (default) or ``clang``, pinned through QEMU's own ``--cc`` and
   ``--cxx`` (see the toolchain note below: the environment ``CC`` does not
   work here).

``ccache`` and ``ccache_max_size``
   Compile through ccache the documented QEMU way
   (``--cc="ccache <cc>"``, word-split into the meson compiler array). On by
   default with a 10 GiB cache, driven by the shared ``write_ccache_conf``
   helper in ``f/common/devshell`` that the kernel build also uses.

``compile_commands``
   Copy meson's auto-generated ``compile_commands.json`` into the source root
   so ``clangd`` indexes the out-of-tree build (the ``devtools`` step). On by
   default.

``configure_args``
   Free-form extra ``--enable-*`` and ``--disable-*`` flags.

``shared``
   ``false`` (default, this worker's own tree) or ``true`` (a shared named
   tree), with the same semantics as the kernel build.

The source URL is not a flow input: it is fixed by the mirror, exactly as the
kernel build takes a ref but not a URL. Build parallelism is
``make -j$(nproc)``, governed by the container cgroup so concurrent builds
self-balance.

Toolchain notes
===============

The ``.#build`` devShell tracks nixpkgs, whose GCC, Clang/LLVM, and libraries
run ahead of QEMU releases. For the wider toolchain picture see
:doc:`/reference/kernel-toolchains`.

Werror against new libraries
----------------------------

QEMU builds with ``-Werror``, so an older ``qemu_ref`` can fail on a warning
emitted by a newer library or compiler. For example, v9.2.0's ``block/curl.c``
passes an ``int`` where a recent curl wants a ``long``, which is fatal under
``-Werror`` and is fixed in later QEMU. Prefer a recent ``qemu_ref``; for an
older one, pass ``configure_args: "--disable-werror"`` (or ``--disable-curl``).
Both v11.0.0 and v9.2.0 (the latter with ``--disable-werror``) have been
validated end to end into a runnable ``qemu-system-x86_64``.

Compiler selection
------------------

The devShell exports ``CC=clang``, and that wins the cc-wrapper's ``CC`` slot
over GCC and overrides any ``CC`` set in the environment. The compiler must
therefore be passed through QEMU's own ``--cc`` and ``--cxx``, which
``configure`` applies during argument parsing and so beats the devShell. (The
kernel build is immune because it passes ``CC=`` as a make variable.) GCC, the
default, builds clean. Clang/LLVM additionally gets ``-Qunused-arguments``
(via ``--extra-cflags`` and ``--extra-ldflags``) to silence the spurious
``-Wunused-command-line-argument`` it emits on link steps for the devShell's
GCC-oriented ``-Wa,--compress-debug-sections``.

The output contract
====================

``collect`` writes a manifest that becomes the flow result, parallel to the
kernel build's manifest:

.. code-block:: json

   {
     "qemu_binary": "WORKERS_DIR/<slot>/qemu/destdir/bin/qemu-system-x86_64",
     "version": "<configure-reported version>",
     "target_list": "x86_64-softmmu",
     "commit": "<resolved sha>",
     "ref": "<qemu_ref>",
     "destdir": "WORKERS_DIR/<slot>/qemu/destdir"
   }

This is a provider-agnostic contract: anything that produces a ``qemu_binary``
path (this flow, or the future Nix-derivation variant) satisfies it, so the
guest layer consumes the manifest without knowing how QEMU was built.

How the guest layer consumes this
=================================

The QEMU/systemd guest layer renders a ``qemu-system@<vm>.service`` unit plus
its ``virtiofsd@.service`` into the user systemd manager, and that unit
consumes both build flows: ``qemu_binary`` from `f/qemu/build`_ becomes the
unit's ``ExecStart=`` emulator, while the ``bzImage`` and modules from
`f/kernel/build`_ become ``-kernel`` and the virtiofs ``/lib/modules`` share.
Because both manifests' paths live under ``WORKERS_DIR``, bind-mounted at the
same absolute path on host and container, the host-forked unit resolves them
directly; the host distro QEMU is never referenced. For inspecting a running
guest see :doc:`/flows/guests`.

Future variant: QEMU as a Nix derivation
========================================

The reproducible end state is a ``qemu_binary`` that is a ``/nix/store`` path
rather than a ``WORKERS_DIR`` destdir. Two routes get there, both deferred.
Consuming nixpkgs ``pkgs.qemu`` directly (already in the flake) is the default
when no custom version is needed. Building a specific ref as a derivation
(``pkgs.qemu.overrideAttrs`` with ``src = <ref>``) gives a custom version and a
hermetic store path at once.

Either route slots in as a second method inside `f/qemu/build`_ through a
``branchone``, mirroring how the kernel build offers its config methods. It
emits the same ``result.json`` (with ``qemu_binary`` as a store path), so the
guest layer is unchanged. The meson-to-destdir method documented here ships
first, and the derivation method follows.

.. _f/qemu/build:
   https://github.com/dagomez137/kdevops-ng/tree/main/f/qemu/build.flow
.. _f/kernel/build:
   https://github.com/dagomez137/kdevops-ng/tree/main/f/kernel/build.flow
.. _f/workbench/fetch:
   https://github.com/dagomez137/kdevops-ng/tree/main/f/workbench/fetch.flow

.. _QEMU: https://www.qemu.org/
