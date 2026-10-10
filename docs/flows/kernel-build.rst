.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

======================
Build the Linux kernel
======================

The :src:`f/kernel/build` flow builds a custom `Linux kernel`_ from source,
reproducibly, to boot in a QEMU virtual machine run through systemd. It is the
Windmill equivalent of a ``make`` of an upstream kernel at a pinned ref,
optionally with a mailed patch series applied on top. The flow runs over a
mirror-backed git
worktree inside the ``nixos-flake`` ``.#build`` devShell, with
``make --jobs=$(nproc)``, so one build uses every CPU and concurrent builds
share them per build, through the worker units' equal CPU weight, and returns
a manifest a downstream flow reads. :src:`f/qemu/build` deliberately mirrors
it for QEMU.

The flow
========

The flow is a ``same_worker`` pipeline, so every step runs on the one worker
and sees the previous step's files:

::

   prepare_worktree -> build_flags -> configure -> fetch_identity ->
   reuse_check -> compile -> devtools -> install -> install_modules ->
   publish -> publish_devel -> deploy_worktree -> fetch_devel ->
   [checks] -> collect

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
     - Add this identity's devel layer (the build dir's ``.cmd`` files, the
       generated headers and sources, the kconfig files a Rust index run
       reads and ``Module.symvers``, minus every binary) to the Nix store so
       ``fetch_devel`` can index a worktree; only after a real build.
     - Host
   * - ``deploy_worktree``
     - Lay the developer-group worktree at the built ref; only when a developer
       worktree is requested.
     - Host
   * - ``fetch_devel``
     - Materialize the devel layer into that developer worktree and regenerate
       both its ``clangd`` and its ``rust-analyzer`` index.
     - ``.#transfer``, then
       ``.#build-kernel``
   * - checks
     - The optional pre-submission checks, one step per check; see
       `Pre-submission checks`_.
     - ``.#check-kernel``
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
QEMU build, see :src:`f/workbench/fetch` and :doc:`/concepts/build-store`.

Schema inputs
=============

The form surfaces the choices a kernel developer actually makes, grouped by
concern. The group labels (Worktree, Configuration, Build, Reuse, Installation)
carry their own one-line summaries in the form; this section is the full
reference.

Worktree
--------

``ref``
   The branch or tag to build, picked from the Bare's live ref list:
   developer branches first, then the mirror remote's branches as
   ``mirror/<branch>``, then tags newest first, headed by the current
   kernel.org releases from `releases.json`_ (mainline, stable, longterm,
   linux-next), each labelled with its moniker plus EOL state and whether
   the Bare mirrors it yet. The picker shares the Git Ref label with its
   free-text twin and sits after the ``custom_ref`` toggle, so switching
   swaps the widget in place.

``custom_ref`` and ``git_ref``
   The advanced override for anything the picker cannot offer. Turn on
   ``custom_ref`` and ``git_ref`` replaces the picked ref: resolved against
   a tag, then the ``mirror`` remote, then the literal ref, so ``v7.1``,
   ``mirror/master``, ``hch-misc``, or a bare SHA all work.

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

``deploy_developer_worktree`` and the group knobs
   Drive the optional developer-worktree tail (``deploy_worktree``,
   ``fetch_devel``): lay a checkout of the built ref under a worktree group for
   a human to open in an editor, indexed by the fetched devel layer. The group
   is not asked for by default; it takes the build's own auto-derived name, so
   a stock tag deploys into ``vanilla``, a ``b4`` series into a group named for
   the series title, and a branch into one named for the ref. ``vanilla``
   asserts an unmodified baseline (ADR-0008), so a patched tree must not land
   there by default. Turn on ``custom_group`` to name it, which is also how the
   same group comes to hold both ``linux`` and ``qemu``; see
   :doc:`qemu-build`.

   The tail never discards work and never fails the build. Its whole vocabulary
   is ``git worktree add``, a plain ``git checkout`` of a branch, and
   ``git merge --ff-only``: no forcing verb, so every command git itself refuses
   rather than clobbers. It decides by what it finds:

   .. list-table::
      :header-rows: 1
      :widths: 40 60

      * - The worktree is
        - What happens
      * - already on the branch, at the built commit
        - nothing at all, not one git command
      * - at the built commit but detached
        - checked out onto the branch, carrying modified and staged files
          across
      * - behind the built commit, detached
        - fast-forwarded, which can neither rewind nor orphan a commit
      * - on a branch, not at the built commit
        - left alone. A developer's branch ref is never moved, not even forward
      * - mid-rebase, mid-``am``, or not a worktree of this Bare
        - left alone

   Where it leaves the tree alone it prints the one specific reason and reports
   ``synced: false``, and the run continues: this is the tail of a build that
   already compiled and published, so a dirty tree must not cost you that. The
   indexes are then skipped too, because indexing a tree at a different commit
   would describe your source with the build's command database and hand you an
   index that looks complete and is not.

``recreate_developer_worktree``
   Re-cut the developer group worktree instead of reusing it. It removes only a
   worktree of this Bare: a path that is something else is reported and never
   removed, whatever this is set to.

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

``extra_warn``
   kbuild's extra warning levels, combined into one ``W=``: ``1``, ``2``,
   ``3``, and ``e`` to make them errors (``W=12e``).

``kcflags``
   Extra C compiler flags, from a curated set: ``-W``, which
   ``Documentation/process/submit-checklist.rst`` asks new code to be compiled
   with, and ``-Wno-error`` for a Build configured with ``CONFIG_WERROR``.

``make_flags``
   Advanced: free-form extra make variables and flags, appended verbatim after
   the picks above. A ``KCFLAGS`` here merges with ``kcflags``.

All three are build flags, so they are part of the Build identity.

``reproducible``
   Pin ``KBUILD_BUILD_TIMESTAMP``/``USER``/``HOST`` for a reproducible build
   (on by default).

``timestamp_from_commit``
   Derive the reproducible timestamp from the source commit date.

``ccache`` and ``ccache_max_size``
   Compile through ccache (``CC="ccache <cc>"``, a shared ``CCACHE_DIR``), on by
   default with a 10 GiB cache, driven by the shared ``write_ccache_conf``
   helper in :src:`f/common/devshell` that the QEMU build also uses.

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

Checks
------

``enable``
   Run the pre-submission checks after the Build is published (off by
   default). Turns off the reuse skip for ``compile``, since every check reads
   a built Build.

``checks``
   The checks to run, by the upstream tool's name. The default set is fast
   enough for every series; ``coccicheck``, ``clang-tidy``, ``clang-analyzer``
   and ``htmldocs`` are opt-in.

``base``
   The upstream ref the series sits on, a tag (``v7.2``) or a mirror branch
   (``master``). Empty works only with a ``b4_series``, where the checked-out
   ref is the base.

``scope``
   ``series`` narrows the checks that can read every file to the files the
   series touches; ``worktree`` checks every file, as a maintainer does before
   pushing.

``subject_prefix``, ``strict``
   The patch subject prefix (``PATCH v2``), and ``--strict`` for
   :cmd:`checkpatch.pl`.

The source URL is not a flow input: it is fixed by the mirror, exactly as the
QEMU build takes a ref but not a URL.

Configuration methods
=====================

``configure`` is a ``branchone`` over ``config_method``, so exactly one of three
steps produces ``.config``:

``preset``
   :src:`f/kernel/configure_preset` applies a predefined whole-kernel config
   from the library through the kernel's own ``KCONFIG_ALLCONFIG`` mechanism
   (``make KCONFIG_ALLCONFIG=<file> alldefconfig``), which forces the preset's
   symbols and defaults the rest. This is the zero-config path.

``make``
   :src:`f/kernel/configure_make` runs one or more in-tree config goals
   (``defconfig``, ``tinyconfig``, ``kvm_guest.config``, ...) the ordinary way.

``fragments``
   :src:`f/kernel/configure_fragments` merges curated fragments from
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
inferred, in this precedence: a ``custom_label`` override; else, for a ``b4``
series, the series-root (cover) subject as a slug, carrying the revision; else
``vanilla`` for an upstream tag checked out with no series; else a slug of the
``git_ref`` (a branch or SHA). The label is truncated to fit the 64-character
``uname -r``; the digest is never shortened.

For a ``b4`` series the cover letter is fetched on its own, with
``b4 mbox --single-message`` of the message-id, because that subject holds the
series title and revision that the patch mbox lacks (``b4 am`` does not save the
cover); a failed fetch falls back to the first patch subject. The revision
``N`` is read from a ``[PATCH vN M/K]`` bracket, or from a standalone ``vN`` at
the very end of the subject (the ``... v3`` convention), and appended as
``-v<N>`` only when a version is actually present and is v2 or later. A series
with no version token gets no suffix; no revision is invented. A matched
``-v<N>`` is preserved across truncation, so it is never the part that is cut.
So an iomap series whose cover reads ``don't build bios/contexts over multiple
iomaps v3`` builds as
``7.1.0-don-t-build-bios-contexts-over-multiple-v3-<digest>``, reading apart
from its v2 at a glance, while the digest already tells them apart by content.

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
and ``publish``: the manifest points at the existing copy rather than rebuilding
it. ``publish_devel`` short-circuits on the **devel** layer's own presence
instead, which ``reuse_check`` reports as ``devel_present``, because the two
layers are independent store paths. When a developer worktree is requested and
the devel layer is missing, the run-layer hit is not accepted: ``compile`` and
``publish_devel`` run so there is a layer to index with, while ``install`` and
``publish`` still skip. Refs and the build inputs cross hosts by ``git``;
the run-layer outputs cross by ``nix copy``. See :doc:`/concepts/build-store`.

Indexing a developer worktree
=============================

With **Deploy Developer Worktree** on, the tail of the flow lays the group
worktree at the built ref and leaves two indexes in it,
``compile_commands.json`` for ``clangd`` and ``rust-project.json`` for
``rust-analyzer``. Both are
regenerated locally against that worktree's own source, so neither carries a
builder path, and both are named in the kernel's own ``.gitignore``, so neither
dirties ``git status``. The Rust index appears only for a ``CONFIG_RUST=y``
kernel; otherwise the step says so and leaves the C index alone.

Proc macros need no editor configuration at all, and adding some is actively
harmful. The devel layer carries no compiled output, so the three proc-macro
dylibs (``libmacros.so``, ``libpin_init_internal.so``,
``libzerocopy_derive.so``) are not in it; ``fetch_devel`` compiles them in the
same devShell that regenerates the index, and reports how many the index
resolved:

.. code-block:: text
   :caption: host
   :class: cmd-host

   wrote <worktree>/rust-project.json (46 crates, 3/3 proc-macro dylibs present)

A proc-macro dylib loads only into a proc-macro server built by the very same
``rustc``, which is why they are compiled rather than shipped. Nothing has to
be told where that server is. ``rust-analyzer`` reads the ``sysroot`` field of
``rust-project.json`` and starts that sysroot's own
``libexec/rust-analyzer-proc-macro-srv`` itself, and because one devShell
writes both that field and the dylibs, the two move together on every
toolchain bump.

.. warning::

   Do not set ``rust-analyzer.procMacro.server``. It is an override for a value
   the index already carries correctly, it pins a store path that the next
   toolchain bump invalidates, and in an editor's global configuration it
   applies to every Rust workspace, so it breaks proc macros in ordinary Cargo
   projects whose crates were built by a different ``rustc``. Setting
   ``RUSTUP_TOOLCHAIN`` forces the same breakage by another road.

A ``0/3`` count means the dylibs could not be built, and the line above it says
why. The index is still correct and everything except macro expansion resolves
from it, but ``module!``, ``#[vtable]``, ``#[pin_data]`` and ``pin_init!`` will
each raise a ``macro-error`` at their use sites until it is fixed.

Pre-submission checks
=====================

With ``checks.enable`` set, the flow runs the mechanical part of the kernel's
pre-submission checklist over the series on top of the Build, for C and for
Rust, after ``publish_devel``. A developer runs it on a branch before
:cmd:`git send-email`; a maintainer runs it on a series received from lore,
applied with :cmd:`b4`. :doc:`/reference/process-checklist` maps each check to
the sentence of the kernel's documentation that asks for it, and lists what is
left to a person. Bringup composes this flow, so the same Checks group appears
under its Kernel component.

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Step
     - Action
   * - :src:`format_patch <f/kernel/format_patch>`
     - Find the merge base of ``base`` and HEAD, then :cmd:`git format-patch`
       ``--base`` the commits above it into the Worker sandbox's
       ``check/patches/``, with a cover letter for more than one patch.
       ``check/`` is emptied first; every later step writes its log there.
   * - :src:`select_checks <f/kernel/select_checks>`
     - Resolve the picked checks against the configured Build: the Rust ones
       need ``CONFIG_RUST=y``, the Clang analyzers a Clang Build. A check the
       Build cannot take is skipped, with the reason in the report.
   * - :src:`check_warnings <f/kernel/check_warnings>`
     - Rebuild with ``W=1``: the series' objects, or every object.
   * - :src:`check_style <f/kernel/check_style>`
     - ``scripts/checkpatch.pl --terse --show-types [--strict]`` over the
       patches.
   * - :src:`check_sparse <f/kernel/check_sparse>`
     - ``make C=2`` with :cmd:`sparse` over the series' objects, or every
       file. kbuild rejecting the devShell's ``sparse`` fails the step instead
       of passing with nothing checked.
   * - :src:`check_stack <f/kernel/check_stack>`
     - ``make checkstack``; frames over 512 bytes in the series' own functions
       are counted.
   * - :src:`check_kerneldoc <f/kernel/check_kerneldoc>`
     - :cmd:`kernel-doc` ``--none -Wall`` over the series' C files and
       headers, which ``W=1`` covers only in part.
   * - :src:`check_uapi <f/kernel/check_uapi>`
     - :cmd:`check-uapi.sh` between the base and HEAD, when the series touches
       a UAPI header.
   * - :src:`check_cocci <f/kernel/check_cocci>`
     - `Coccinelle`_'s semantic patches in report mode (:cmd:`coccicheck`),
       one rule at a time, so a rule the devShell's Coccinelle cannot parse is
       listed in ``rules_failed`` instead of stopping the rest. Opt-in.
   * - :src:`check_clang <f/kernel/check_clang>`
     - :cmd:`clang-tidy` and the Clang Static Analyzer over the series' part of
       ``compile_commands.json``. Opt-in; needs a Clang Build.
   * - :src:`build_htmldocs <f/kernel/build_htmldocs>`
     - ``make htmldocs``, narrowed with ``SPHINXDIRS`` to the Documentation/
       subdirectories the series touches. Opt-in.
   * - :src:`check_rustavailable <f/kernel/check_rustavailable>`
     - ``make rustavailable``.
   * - :src:`check_rustfmt <f/kernel/check_rustfmt>`
     - ``make rustfmtcheck`` (:cmd:`rustfmt`).
   * - :src:`check_clippy <f/kernel/check_clippy>`
     - ``make CLIPPY=1``, which rebuilds the Rust objects through
       :cmd:`clippy-driver`.
   * - :src:`build_rustdoc <f/kernel/build_rustdoc>`
     - ``make rustdoc`` (:cmd:`rustdoc`).
   * - :src:`run_rusttest <f/kernel/run_rusttest>`
     - ``make rusttest``, the Rust host tests.
   * - :src:`find_maintainers <f/kernel/find_maintainers>`
     - :cmd:`get_maintainer.pl` over the patches, kept as ``maintainers.txt``.
   * - :src:`report_checks <f/kernel/report_checks>`
     - Write ``report.json`` and the report tables.
   * - :src:`judge_checks <f/kernel/judge_checks>`
     - Fail the run when a check failed or raised.

The checks never change the Build identity. The flags they need are their own
make's, not the Build's: ``W=1``, and ``KCFLAGS=-Wno-error`` with
``KRUSTFLAGS=--cap-lints=warn`` so that a Build configured with
``CONFIG_WERROR`` counts its warnings instead of stopping on the first. They
run after ``publish``, so nothing they rebuild (`Clippy`_'s objects among them)
is installed; the next build of the identity recompiles those objects. Every
check runs in the ``check-kernel`` devShell of the vendored nixos-flake: the
``build-kernel`` toolchain plus Clippy, Coccinelle and the `OCaml`_ its scripted
rules compile with, `libabigail`_, `Sphinx`_ and a Sparse new enough for
current kernels.

One rule decides a check, the one
``Documentation/process/submitting-patches.rst`` applies to checkpatch: an
error fails it, and a warning is counted for the author to justify. A check
step never fails on its findings; it returns them with a ``status`` and the
flow continues past it (``continue_on_error``), so a failed check still carries
its counts. ``judge_checks`` turns any failed check into a failed run, and in
bringup that stops the run before the guest boots. Where a tool has no error
level of its own, the step picks one: a ``rustfmtcheck`` hunk fails, since
formatting is not a judgment call, and so does a ``check-uapi.sh`` ABI
difference; ``checkstack`` and Coccinelle never fail.

``report_checks`` returns four tables: the series (base, commits, scope,
patches, report path); one row per check with its status, counts and log;
every finding, errors first; and the recipients. ``report.json`` holds every
step's full result:

.. code-block:: text

   $WORKERS_DIR/<worker>/check/
   ├── patches/           0000-cover-letter.patch, 0001-*.patch, ...
   ├── checkpatch.log     one file:line: LEVEL:TYPE: line per finding
   ├── warnings.log       the W=1 rebuild
   ├── sparse.log         the make C=2 output
   ├── ...                one log per check that ran
   ├── maintainers.txt    get_maintainer.pl's list
   └── report.json        every step's result

To check a developer branch:

.. code-block:: bash
   :class: cmd-host

   wmill flow run f/kernel/build --data '{
       "worktree": {"custom_ref": true, "git_ref": "my-topic"},
       "checks": {"enable": true, "base": "v7.2"}
   }'

and a series on lore, applied to its base:

.. code-block:: bash
   :class: cmd-host

   wmill flow run f/kernel/build --data '{
       "worktree": {"custom_ref": true, "git_ref": "v7.2",
                    "b4_series": "<message-id>"},
       "checks": {"enable": true}
   }'

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
consumes both build flows: ``qemu_binary`` from ``f/qemu/build`` becomes the
unit's ``ExecStart=`` emulator, while ``bzImage`` from this flow becomes
``-kernel`` and the ``/lib/modules`` tree becomes a ``virtiofs`` share the guest
mounts at ``/lib/modules/$(uname -r)``. Because the booted kernel's
``uts_release`` is the unique build identity, the modules share resolves to that
exact release, so module autoload (virtio-vsock, virtiofs, and the rest) lines
up with the running kernel. For inspecting a running guest see
:doc:`/flows/guests`.

.. _Linux kernel: https://www.kernel.org/
.. _releases.json: https://www.kernel.org/releases.json
.. _linux-config-fragments: https://github.com/dagomez137/linux-config-fragments
.. _b4: https://b4.docs.kernel.org/
.. _Coccinelle: https://coccinelle.gitlabpages.inria.fr/website/
.. _libabigail: https://sourceware.org/libabigail/
.. _Sphinx: https://www.sphinx-doc.org/
.. _Clippy: https://doc.rust-lang.org/clippy/
.. _OCaml: https://ocaml.org/
