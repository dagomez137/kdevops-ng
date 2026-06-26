.. SPDX-License-Identifier: copyleft-next-0.3.1

===========
Terminology
===========

The ubiquitous language for how build sources, artifacts and collaborators
are organized and shared between people and the `Windmill`_ engine.

.. _Windmill: https://www.windmill.dev/

.. note::

   A :term:`Workbench` is **not** a Windmill *workspace*. A workbench is a
   build sandbox on disk; the Windmill *workspace* (the ``kdevops``
   workspace-as-code) holds flow and script content. They are different kinds
   of thing and the two words are never used interchangeably.

How the terms relate
====================

A :term:`Developer` publishes a `git reference
<https://git-scm.com/book/en/v2/Git-Internals-Git-References>`__, or *ref*, to
the :term:`Bare`, and a :term:`Worker` consumes that ref into its own
:term:`Worktree`. Each Worktree is paired with a :term:`Build`, and each Build
is identified by a :term:`Build identity`, which keys the :term:`Store`.

The containment of the on-host pieces is:

::

   workbench/             the Workbench (relocatable; or $HOME/src)
   ├── system/         reserved: bare/ mirror/ ssh/ store-index/ ccache/ gitbin/
   ├── workers/<id>/      reserved: per-worker build sandboxes
   │   └── main/          the worker's fixed group
   │       └── linux/     the worker worktree (build site)
   ├── vanilla/           the default worktree-group
   │   ├── linux/         a project worktree
   │   │   ├── build/     child of the source
   │   │   └── destdir/   install staging
   │   └── qemu/          a project worktree
   │       ├── build/
   │       └── destdir/
   └── largeio/           a further worktree-group
   vendor/                pinned upstream projects

The whole ``workbench/`` relocates as a unit; ``system/`` and ``workers/`` each
relocate on their own, since they are infrastructure rather than developer
content, and the worktree-groups likewise relocate as a set, defaulting as the
Workbench's direct children but pointable elsewhere together. A workbench need
not sit inside this project: point it at any directory such as ``$HOME/src``,
and that directory becomes the workbench, with worktree-groups directly under
it (so ``$HOME/src/vanilla``). The names
``system`` and ``workers`` are reserved, so a worktree-group may not take them.
``vendor/`` holds the pinned upstream projects and stays a top-level sibling.

A project gains several worktrees by appearing in several worktree-groups:
``vanilla/linux`` and ``largeio/linux`` are two worktrees of one ``linux``,
both cut from the single ``system/bare/linux.git``.

Actors
======

.. glossary::

   Worker
      A Windmill worker unit; the build executor. A worker always builds in
      its own :term:`Worker sandbox` and never modifies a :term:`Developer`'s
      :term:`Worktree`.

      Avoid: *builder*.

   Developer
      A human authorized to drive the Windmill engine through its CLI or UI:
      to create :term:`Worktrees <Worktree>`, publish refs, and so on. A
      developer hands work to a :term:`Worker` only by publishing a ref to the
      :term:`Bare`.

      Avoid: *operator*, *user*.

Places
======

.. glossary::

   Workbench
      A directory containing a :term:`Developer`'s :term:`Worktree-groups
      <Worktree-group>` and the kdevops-ng infrastructure (the :term:`System
      workbench` and the :term:`Worker sandboxes <Worker sandbox>`) that
      defaults under it. It relocates as a whole (default ``workbench/``, or
      for example ``$HOME/src``), and the infrastructure relocates on its own.
      It is *not* a Windmill workspace.

      Avoid: *workspace*, *sandbox*, *bench*.

   Worktree-group
      A topic or chain of work within a :term:`Workbench` (default name
      ``vanilla``; many may exist, such as ``largeio``). It holds one
      :term:`Worktree` per project the topic involves, and the developer
      switches between groups. The groups are the Workbench's direct children
      by default and relocate as a set on their own.

      Avoid: *workbench*, *default*.

   System workbench
      The host-local infrastructure singleton: the :term:`Mirrors <Mirror>`,
      :term:`Bares <Bare>`, SSH key, :term:`Store`, the shared compiler cache,
      and the Store index (the identity->store-path GC-root registry). It
      defaults to ``system/`` under the :term:`Workbench` but relocates on its
      own, and its :term:`Mirrors <Mirror>` (the bulky shared object store,
      default ``system/mirror``), compiler cache (default ``system/ccache``) and
      Store index (default ``system/store-index``, relocatable via
      ``STORE_INDEX_DIR``) relocate apart from it again. It is user-scoped and
      sudo-less in steady state.

      Avoid: *service workbench*.

   Worker sandbox
      A :term:`Worker`'s own build area (default ``workers/<id>/``,
      relocatable on its own). Inside it the worker keeps one worker worktree
      per project under the fixed ``main`` group
      (``workers/<id>/main/<project>``). A worker builds here, never in a
      :term:`Developer`'s :term:`Worktree`.

      Avoid: *workbench*, *worker dir*.

   Project name
      A project's upstream source-directory name, such as ``linux`` or
      ``qemu``. The project folder within a :term:`Worktree-group` is named by
      it.

      Avoid: *source name*.

Source and artifacts
====================

.. glossary::

   Worktree
      A ``git`` checkout of a project within a :term:`Worktree-group`, the
      folder named by its :term:`Project name`, created with
      `git-worktree(1) <https://git-scm.com/docs/git-worktree>`__; the project
      abbreviates *git-worktree* to *worktree*. The project keeps two kinds. A
      *worker worktree* is the build site: it lives in a :term:`Worker sandbox`
      at ``workers/<id>/main/<project>`` under the worker's fixed ``main``
      group, is re-synced to the ref on every build, and is tunable only by
      wipe and reinitialize. A *developer worktree* is developer-owned, under
      ``WORKTREES_DIR/<group>/<project>``, and is the checkout a developer
      fetches a build's devel layer into for editor indexing; whether one
      exists is independent of where the build ran. A :term:`Worker` never
      modifies a :term:`Developer`'s worktree. A project gains several developer
      worktrees by appearing in several worktree-groups.

      Avoid: *tree*, *checkout*.

   Build
      The build directory paired with one :term:`Worktree`. By default it is a
      child of the source worktree so kbuild emits relative paths and
      artifacts relocate across hosts with no rewrite. There is one Build per
      worktree.

      Avoid: *build dir*, *O=*.

   Mirror
      A *disposable* local cache of an upstream, force-refreshed on a timer. A
      pure ref and object source: it never holds :term:`Worktrees <Worktree>`
      or development branches. It lives at ``system/mirror`` by default and,
      being the bulky shared object store, relocates on its own.

      Avoid: *cache*, *clone*.

   Bare
      The *durable* working repo at ``system/bare/<project-name>.git``: it
      holds development branches and all :term:`Worktrees <Worktree>`, borrows
      the :term:`Mirror`'s objects, and pulls the Mirror's refs into a remotes
      namespace. It is never force-pruned, and there is one per host. The Bare
      is the ref channel between a :term:`Developer` and a :term:`Worker`.

      Avoid: *remote*, *clone*.

   Build identity
      A content hash of a :term:`Build`'s inputs: config, toolchain (the Nix
      devShell's store hash), build flags, and source commit. Same identity
      implies the same bytes. Where it can, a project bakes the identity into
      its artifact so the result self-reports it; the kernel, for example, puts
      it in ``kernelrelease`` via ``LOCALVERSION``.

      Avoid: *build hash*, *release*.

   Store
      Maps a :term:`Build identity` to its built artifacts, so a host that
      lacks an identity *fetches* it from a peer instead of rebuilding; fetch
      beats build. The artifacts live in the Nix store (added with ``nix store
      add-path``, fetched with ``nix copy``) and are indexed by identity,
      because a ``make`` build is not a Nix derivation and Nix offers no
      pre-build key to skip it.

      Avoid: *destdir*, *artefactory*, *registry*, *releases*.
