# Worker worktrees use a fixed `main` group

ADR-0008 split the flat build area into developer worktree-groups under
`WORKTREES_DIR` and per-worker sandboxes under `WORKERS_DIR`, but it left the
sandbox's internal shape unspecified. The code filled that gap with
`workers/<id>/<project>/main`, putting `main` as a leaf under the project. That
shape diverged from the developer worktree shape `WORKTREES_DIR/<group>/<project>`
for no reason, and a `main` leaf under a project name falsely reads as a
per-project branch namespace rather than what it is, the worker's single
worktree of that project.

Two worktree kinds exist and the difference is load-bearing, so name it. A
**worker worktree** is the build site: it lives inside a worker sandbox, is
re-synced to the requested ref on every build, and is deliberately not tunable
beyond wipe and reinitialize. A developer never reads it; its outputs reach a
developer only through the Store (the run layer and the devel layer). A
**developer group worktree** is the developer-facing checkout under
`WORKTREES_DIR/<group>/<project>`, laid down by `f/workbench/worktree/init`.
Whether one exists is independent of where any build ran: a developer may keep a
group worktree purely to receive a build's devel artifacts for clangd,
rust-analyzer and GDB work, even when the build happened in a worker sandbox on
another host.

Make the worker worktree share the developer shape by giving the worker a single
fixed group named `main`: `workers/<id>/main/<project>`, the direct parallel of
`WORKTREES_DIR/<group>/<project>` with the worker's own sandbox as the root.
Both kinds then resolve through one `<root>/<group>/<project>` formula.

## Status

accepted

## Considered Options

- **`workers/<id>/<project>/main`** (the prior code). Rejected: asymmetric with
  the developer layout, and the `main` leaf misreads as a branch namespace.
- **A configurable worker group.** Rejected: a worker carries no topic or chain
  of work, so a per-worker group name would be a knob with no meaning. The group
  is fixed.
- **`workers/<id>/main/<project>` with a fixed `main` group** (chosen). One path
  formula for both kinds, and the layout is self-describing: a worker is a
  sandbox whose only group is `main`.

## Consequences

- `f/common/worktree.py` resolves both kinds with the same
  `<root>/<group>/<project>` expression; the worker root is `WORKERS_DIR/<id>`
  and its group is the literal `main`.
- `main` is the conventional worker group, not a reserved name. A developer may
  still name a group `main`; the two never collide because they root under
  different trees (`WORKERS_DIR/<id>` versus `WORKTREES_DIR`). Only `system` and
  `workers` stay reserved (ADR-0008).
- The on-disk move from the prior shape is self-healing: the next build at the
  new path lays a fresh detached worktree and prunes the stale admin entry, so
  no git surgery is needed. The only cost is one cold rebuild, since no durable
  state lives in the sandbox.
- The worker-versus-developer distinction is now explicit, which lets a build
  flow keep its worker build-site knobs (wipe, recreate) separate from a
  developer-worktree deploy step that fetches the devel layer into a group
  worktree.
