.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

======================
Build the Linux kernel
======================

The `f/kernel/build`_ flow builds a custom `Linux kernel`_ from source,
reproducibly, to boot in a QEMU virtual machine run through systemd. It is the
Windmill equivalent of a ``make`` of an upstream kernel at a pinned ref,
optionally with a mailed patch series applied on top. The flow runs over a
mirror-backed git
worktree inside the ``nixos-flake`` ``.#build`` devShell, with
``make --jobs=$(nproc)`` so the container cgroup governs CPU and concurrent
builds self-balance across workers, and returns a manifest a downstream flow
reads. `f/qemu/build`_ deliberately mirrors it for QEMU.

The flow
========

The flow is a ``same_worker`` pipeline, so every step runs on the one worker
and sees the previous step's files:

::

   prepare_worktree -> build_flags -> configure -> fetch_identity ->
   reuse_check -> compile -> devtools -> install -> install_modules ->
   publish -> publish_devel -> deploy_worktree -> fetch_devel -> collect

.. list-table::
   :header-rows: 1
   :widths: 22 52 14

   * - Step
     - Action
     - Runs in
   * - ``prepare_worktree``
     - Sync this worker's warm ``main`` worktree to ``git_ref`` off the Bare,
       optionally applying a ``b4`` series; create ``build/`` and ``destdir/``.
     - Host
   * - ``build_flags``
     - Resolve the make flags: the toolchain (GCC or clang/LLVM),
       reproducibility, and ccache, writing the managed ccache config.
     - Host
   * - ``configure``
     - Generate ``.config`` by the chosen method, then bake the build identity
       into ``kernelrelease`` (see `Build identity and reuse`_).
     - ``.#build``
   * - ``fetch_identity``
     - With ``use_peers``, fetch this identity's run layer from a registered
       peer's Nix store so a local reuse hit can land. Otherwise a no-op.
     - ``.#build``
   * - ``reuse_check``
     - Report whether this identity's image and modules are already present, in
       the worker ``destdir`` or this host's Nix store.
     - Host
   * - ``compile``
     - ``make`` (default goal: ``vmlinux``, the arch boot image, and the
       modules). Skipped on a reuse hit.
     - ``.#build``
   * - ``devtools``
     - Generate ``compile_commands.json``, the GDB helpers, and
       ``rust-project.json``; each optional, on by default.
     - ``.#build``
   * - ``install``
     - ``make install`` into ``destdir/``; only when ``install`` is set, and
       skipped on reuse.
     - ``.#build``
   * - ``install_modules``
     - ``make modules_install`` into ``destdir/`` plus the canonical
       ``source`` symlink; only when ``modules_install`` is set, skipped on
       reuse.
     - ``.#build``
   * - ``publish``
     - Add this identity's run layer (the boot image and ``/lib/modules`` tree)
       to the Nix store so a peer can fetch it; only after a real install.
     - Host
   * - ``publish_devel``
     - Add this identity's devel layer (the build dir's ``.cmd`` files,
       generated headers and scripts, minus binaries) to the Nix store so
       ``fetch_devel`` can index a worktree; only after a real build.
     - Host
   * - ``deploy_worktree``
     - Lay the developer-group worktree at the built ref; only when a developer
       worktree is requested.
     - Host
   * - ``fetch_devel``
     - Materialize the devel layer into that developer worktree and regenerate
       its ``clangd`` index.
     - ``.#build``
   * - ``collect``
     - Merge the step results into one manifest and return it as the flow
       result.
     - Host

The warm-tree layout keeps the source at
``WORKERS_DIR/<WORKER_INDEX>/main/linux`` with ``build/`` and ``destdir/`` as
children of it. Re-syncing to ``git_ref`` on every build keeps rebuilds
incremental, and because each worker has its own warm tree, builds on different
workers run in parallel. Everything lives under ``WORKERS_DIR``, bind-mounted at
identical host paths, so a host-forked process (the guest's QEMU) reads the
artifacts directly. For the durable-Bare provisioning model shared with the
QEMU build, see `f/workbench/fetch`_ and :doc:`/concepts/build-store`.

Schema inputs
=============

The form surfaces the choices a kernel developer actually makes, grouped by
concern. The group labels (Worktree, Configuration, Build, Reuse, Installation)
carry their own one-line summaries in the form; this section is the full
reference.

Worktree
--------

``git_ref``
   The tag, branch, or SHA to check out from the Bare (default a recent stable
   tag). Resolved against a tag, then the ``mirror`` remote, then the literal
   ref, so ``v7.1``, ``mirror/master``, ``hch-misc``, or a bare SHA all work.

``b4_series``
   An optional `b4`_ message-id or lore URL. When set, ``prepare_worktree``
   downloads the mailed series with ``b4 am`` and applies it on top of
   ``git_ref`` with ``git am``, publishing it to the Bare as
   ``refs/heads/b4/<slug>`` for a developer to review.

``custom_label`` and ``label``
   The build identity's name is inferred from the ref and any series (see
   `Build identity and reuse`_), so naming is left off by default. Turn on
   ``custom_label`` to name the build yourself: ``label`` then replaces the
   auto-derived ``vanilla``/series name with your own. It is bounded to 40
   characters and truncated further if the release string would overflow. Use it
   to tag a one-off experiment whose ref or series would not yield a meaningful
   name.

``recreate_build_worktree``
   Lay a fresh detached checkout instead of re-syncing the warm tree, discarding
   ``build/`` and ``destdir/``.

``wipe_build``
   Remove and recreate ``build/`` before configuring, forcing a clean build.

``worktree_group`` and the ``deploy_*`` knobs
   Drive the optional developer-worktree tail (``deploy_worktree``,
   ``fetch_devel``): lay a checkout of the built ref under a named worktree
   group for a human to open in an editor, indexed by the fetched devel layer.

Configuration
-------------

``config_method``
   How ``.config`` is produced: ``preset`` (the default), ``make``, or
   ``fragments``. See `Configuration methods`_.

``preset``
   For ``config_method: preset``, the whole-kernel config to apply, a file in
   the curated `linux-config-fragments`_ ``defconfigs/`` library (default
   ``imageless_defconfig``).

``defconfig``
   For ``config_method: make``, the config goal or list of goals, such as
   ``defconfig`` or ``["defconfig", "kvm_guest.config"]``.

``fragments``
   For ``config_method: fragments``, the curated fragments to merge from the
   `linux-config-fragments`_ library; a canonical merge order is imposed so the
   result is deterministic regardless of selection order.

``allnoconfig_base``
   For ``config_method: fragments``, default unset symbols to ``n`` for a
   minimal, explicit config (on by default).

Build
-----

``targets``
   Extra ``make`` goals to narrow the build; empty by default, so a plain
   ``make`` builds ``vmlinux``, the boot image, and the modules.

``compiler``
   ``gcc`` (default) or ``clang`` (LLVM=1, with the devShell's unwrapped
   clang). For the wider toolchain picture see
   :doc:`/reference/kernel-toolchains`.

``make_flags``
   Free-form extra make variables and flags, appended verbatim (for example
   ``W=1``).

``reproducible``
   Pin ``KBUILD_BUILD_TIMESTAMP``/``USER``/``HOST`` for a reproducible build
   (on by default).

``timestamp_from_commit``
   Derive the reproducible timestamp from the source commit date.

``ccache`` and ``ccache_max_size``
   Compile through ccache (``CC="ccache <cc>"``, a shared ``CCACHE_DIR``), on by
   default with a 10 GiB cache, driven by the shared ``write_ccache_conf``
   helper in ``f/common/devshell`` that the QEMU build also uses.

Reuse
-----

``reuse``
   Skip compile and install when this build identity is already present, in the
   worker ``destdir`` or this host's Nix store. The manifest then points at that
   copy; off forces a rebuild.

``use_peers``
   Before building, fetch this identity's run layer from a registered peer's
   Nix store when one already published it (the peers registry at
   ``$SYSTEM_DIR/peers``). Takes effect only with ``reuse`` on.

Installation
------------

``install``
   ``make install`` the boot image into ``destdir/`` (on by default).

``modules_install``
   ``make modules_install`` into ``destdir/`` (on by default; skip for an
   all-built-in kernel).

``source_symlink``
   Add the canonical ``/lib/modules/<release>/source`` symlink after
   ``modules_install``.

The source URL is not a flow input: it is fixed by the mirror, exactly as the
QEMU build takes a ref but not a URL.

Configuration methods
=====================

``configure`` is a ``branchone`` over ``config_method``, so exactly one of three
steps produces ``.config``:

``preset``
   ``f/kernel/configure_preset`` applies a predefined whole-kernel config from
   the library through the kernel's own ``KCONFIG_ALLCONFIG`` mechanism
   (``make KCONFIG_ALLCONFIG=<file> alldefconfig``), which forces the preset's
   symbols and defaults the rest. This is the zero-config path.

``make``
   ``f/kernel/configure_make`` runs one or more in-tree config goals
   (``defconfig``, ``tinyconfig``, ``kvm_guest.config``, ...) the ordinary way.

``fragments``
   ``f/kernel/configure_fragments`` merges curated fragments from
   `linux-config-fragments`_ with the kernel's ``merge_config.sh``, imposing a
   canonical category order (core, arch, ..., debug) with the ``builtin/``
   ``=y`` overrides last, so the merged ``.config`` is deterministic.

Whichever method runs, it ends by baking the build identity into
``kernelrelease``.

Build identity and reuse
========================

Every build is content-addressed by a **build identity** that ``configure``
bakes into ``kernelrelease`` through ``CONFIG_LOCALVERSION``, so the running
``uname -r`` self-reports it as ``<version>-<label>-<digest>``, for example

::

   7.1.0-vanilla-c0bee73009a8
   \___/ \_____/ \__________/
   version label    digest

The **digest** is a 12-hex hash over the inputs that fix the build's bytes:
the ``.config`` (with the ``LOCALVERSION`` line excluded), the ``.#build``
devShell's toolchain store path, the make flags (with the host-specific
``-fdebug-prefix-map`` value stripped), and the source tree (the worktree's
``HEAD`` tree object). A tree is content-addressed by the file bytes it names,
so a ``b4`` series re-applied with ``git am`` (which restamps each commit with
the wall-clock time, a fresh ``HEAD`` SHA over identical content) still hashes
the same: the identity stays put and reuse holds. The digest is the same on
every host, so a peer's build is provably the one requested, and it is the
field that tells builds apart by content: two builds of one ref with different
configs (KASAN on or off, GCC or clang), or two revisions of one series, differ
in the digest, so they never collide in the Store key or in
``/lib/modules/<release>`` inside the booted guest (the ADR-0002 identity scheme
is intact).

The **label** is the readable name baked in front of the digest. It is
inferred, in this precedence: a ``custom_label`` override; else the ``b4``
series subject as a slug (with ``-v<N>`` appended for v2 and later); else
``vanilla`` for an upstream tag checked out with no series; else a slug of the
``git_ref`` (a branch or SHA). The label is truncated to fit the 64-character
``uname -r``; the digest is never shortened.

The kernel's own ``setlocalversion`` describe suffix (``-<count>-g<sha>``) is
dropped by setting ``CONFIG_LOCALVERSION_AUTO=n`` (this kernel has no
``.scmversion`` mechanism), which frees that length for the label. The commit it
would have named is not lost: it stays in the manifest ``commit`` field, while
the digest keys on that commit's tree.

Because the identity hashes the produced ``.config``, ``configure`` must run
before the build can be matched: ``fetch_identity`` then ``reuse_check`` run
between ``configure`` and ``compile``. ``reuse_check`` resolves
``kernel-<uts_release>`` in the worker ``destdir`` or this host's Nix store
(where a local build published it, or ``fetch_identity`` left a peer's), and a
present identity short-circuits ``compile``, ``install``, ``install_modules``,
``publish``, and ``publish_devel``: the manifest points at the existing copy
rather than rebuilding it. Refs and the build inputs cross hosts by ``git``;
the run-layer outputs cross by ``nix copy``. See :doc:`/concepts/build-store`.

The output contract
===================

``collect`` writes a manifest that becomes the flow result:

.. code-block:: json

   {
     "commit": "<resolved sha>",
     "uts_release": "7.1.0-<label>-<digest>",
     "bzImage": "<destdir-or-store>/boot/<image>-<release>",
     "build_dir": "WORKERS_DIR/<slot>/main/linux/build",
     "config": ".../build/.config",
     "config_method": "preset",
     "destdir": "WORKERS_DIR/<slot>/main/linux/destdir",
     "linux_compiler": "<compiler version>",
     "uts_version": "<uname -v>",
     "uts_machine": "x86_64",
     "linux_compile_by": "kdevops",
     "linux_compile_host": "<reproducible host>"
   }

The provenance fields (``linux_compiler``, ``uts_version``, ``uts_machine``,
``linux_compile_by``, ``linux_compile_host``) are read back from the kernel's
own generated headers, so a mis-quoted input surfaces here rather than
silently. Downstream flows consume the manifest without knowing whether the
build was compiled or reused.

How the guest layer consumes this
=================================

kdevops runs each guest as a ``qemu-system@<vm>.service`` systemd service unit,
an instance of the ``qemu-system@.service`` template unit, and that unit
consumes both build flows: ``qemu_binary`` from `f/qemu/build`_ becomes the
unit's ``ExecStart=`` emulator, while ``bzImage`` from this flow becomes
``-kernel`` and the ``/lib/modules`` tree becomes a ``virtiofs`` share the guest
mounts at ``/lib/modules/$(uname -r)``. Because the booted kernel's
``uts_release`` is the unique build identity, the modules share resolves to that
exact release, so module autoload (virtio-vsock, virtiofs, and the rest) lines
up with the running kernel. For inspecting a running guest see
:doc:`/flows/guests`.

.. _f/kernel/build:
   https://github.com/dagomez137/kdevops-ng/tree/main/f/kernel/build.flow
.. _f/qemu/build:
   https://github.com/dagomez137/kdevops-ng/tree/main/f/qemu/build.flow
.. _f/workbench/fetch:
   https://github.com/dagomez137/kdevops-ng/tree/main/f/workbench/fetch.flow

.. _Linux kernel: https://www.kernel.org/
.. _linux-config-fragments: https://github.com/dagomez137/linux-config-fragments
.. _b4: https://b4.docs.kernel.org/
