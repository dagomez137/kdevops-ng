# Build-area layout: workbench, worktree-groups, relocatable system

The build area on a host was a flat `workers/` directory mixing host-local
infrastructure (`system/`, the vendored toolchain, per-worker sandboxes) with no
place for a developer to keep coherent topics of work. This records the layout we
migrate to, and the naming decisions behind it, so the glossary and the code have
one agreed shape.

## Decision

```
workbench/                 the Workbench: a relocatable build area (default dir;
                           a developer may point it at $HOME/src instead)
  system/                  reserved: bare/  mirror/  ssh/  store/   (relocatable on its own)
  workers/                 reserved: per-worker build sandboxes <id>/ (relocatable on its own)
  vanilla/                 the default worktree-group (a topic)
    linux/                 a project worktree, named by canonical name
      <source>
      build/               child of the source (ADR-0003)
      destdir/             install staging
    qemu/
  largeio/   topic1/ ...   further worktree-groups
vendor/                    pinned upstream projects (ADR-0006, already top-level)
```

A **Workbench** is the developer's build area, a directory that is relocatable as
a whole (default `workbench/`, or wherever the developer puts it, such as
`$HOME/src`). A **worktree-group** is a topic or chain of work inside it (default
name `vanilla`; others named by topic, such as `largeio`); the developer switches
between them. Each worktree-group holds one **worktree** per project the topic
involves, and the project folder is named by its **canonical name** (`linux`,
`qemu`). A project has several worktrees by appearing in several worktree-groups
(`vanilla/linux` and `largeio/linux` are two worktrees of one `linux`, both cut
from the single `system/bare/linux.git`). `build/` and `destdir/` are children of
the source.

`system/` (the host-local infrastructure singleton: mirror, bare, ssh, store) and
`workers/` (per-worker sandboxes) live under the workbench by default but are each
independently relocatable, because they are kdevops-ng infrastructure whereas the
worktree-groups are developer content. A worker always builds in its own sandbox
and never in a developer's worktree.

## Naming decisions

Each name was chosen against alternatives that were rejected for concrete reasons.

**Top directory: `workbench/`** (rejected `root`, `build`, `home`, `workshop`).
`root` is a relative-position word used as a proper name and is not actually the
root of anything (it sits under the repo or `$HOME`), and it collides with `/`,
the `root` user, and `/root`. `build` collides with the leaf `build/` of
ADR-0003 (the word would appear at two depths) and under-describes a tree that
also holds sources, the mirror, the bare, and the store. `home` collides with
`$HOME`, which is worse here because a workbench can legitimately be `$HOME/src`.
`workshop` (the precise hypernym for a container of workbenches) was the runner-up
but applies only if several workbenches coexist under one root; we chose the
single-workbench model (below), so the top directory *is* the workbench.

**Default worktree-group: `vanilla`** (rejected `default`, `upstream`,
`mainline`, `baseline`). `default` names a selection policy, not an identity, and
is category-inconsistent with topic-named siblings like `largeio`; a default is
properly a pointer, not a folder name. `upstream` names the wrong direction (a
working area is downstream) and collides with git's upstream remote and
`@{upstream}`. `mainline` over-pins to the Torvalds tree and mislabels qemu and
xfstests, whose trunks are `master`. `baseline` collides with this repo's own
testing vocabulary (fstests and mmtests compare a candidate against a baseline).
`vanilla` is native kernel idiom for an unmodified baseline, is cross-project
("vanilla qemu", "vanilla axboe-tree" both parse), collides with nothing in the
stack, and its one weakness (it asserts unmodified) doubles as a useful
convention: keep the default group near-pristine and spin a named group for a
real topic.

**Single workbench, topics multiply (Option 2)** over a workbench level that many
worktree-groups share (Option 1). The day-to-day multiplicity is topics, which the
developer switches between; running several workbenches simultaneously is rare.
Promoting worktree-groups to be direct children of the workbench gives a shorter
path (`workbench/vanilla/linux`, not `workbench/src/vanilla/linux`), removes the
need to name a default workbench, and makes the workbench a self-contained unit a
developer relocates or switches as a whole, consistent with the System workbench
being singular and movable. The cost accepted: `system` and `workers` are
**reserved** names a worktree-group may not take.

**Project-namespace is dropped.** It duplicated the canonical name (`kernel` vs
`linux` were 1:1) and existed to bundle several source repos, but a worktree-group
already does that bundling by topic. The project folder is the canonical name, and
the Bare simplifies from `system/bare/<namespace>/<canonical>.git` to
`system/bare/<canonical>.git`.

## Status

accepted

## Consequences

- The single `WORKERS_DIR` (which meant the whole flat root) splits into
  independently-configurable paths so each piece relocates on its own: the
  worktree-group root (default `workbench/`), `system/` (default
  `workbench/system`), and the worker sandboxes (default `workbench/workers`).
  `VENDOR_DIR` (ADR-0006) is unchanged.
- `system/bare/<canonical>.git` replaces the namespaced bare path; Store reuse
  keys by `(worktree-group, canonical)` rather than `(workbench, namespace)`.
- Worker worktree addressing in `f/common/worktree.py` is rewritten: a build
  resolves its worktree under the worker's own sandbox, and a developer worktree
  resolves under `<workbench>/<worktree-group>/<canonical>`.
- `CONTEXT.md` and `docs/terms.rst` are updated: Workbench is redefined as the
  relocatable developer build area, **worktree-group** is added (default
  `vanilla`), Project-namespace is removed.
- This is a migration, sequenced in the project TODO: rename and split the env
  paths first, then introduce the worktree-group layer, preserving the running
  System workbench (mirror, bare, ssh) through the move as the `vendor/`
  relocation was.
