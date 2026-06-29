# The Bare is the working repo; no separate clone

A worktree can only check out refs and objects present in the single repo it
hangs off, so the repo that maintains worktrees must see both upstream refs and
developer-pushed refs. Rather than add a third "working clone" alongside the
Mirror and Bare (what the old `workers/shared/<ns>/<canonical>` clone was), we
collapse that job into the **Bare**: `system/bare/<ns>/<canonical>.git` borrows
the Mirror's objects via alternates, fetches the Mirror's refs into a private
`refs/remotes/mirror/*` namespace, receives developer pushes into `refs/heads/*`,
and is the repo that **all worktrees `git worktree add` off**. We chose the Bare
over the Mirror because the Mirror is disposable (force-fetched and pruned on a
timer) and cannot safely hold worktrees or developer branches, whereas the Bare
is durable and never force-pruned.

## Status

accepted

## Considered Options

- **Worktrees off the Mirror**. Rejected: a dev branch that only lives in the
  Bare can't be checked out, and the Mirror is force-pruned.
- **A third `system/clone` aggregating both**. Rejected: an extra repo to
  provision and keep in sync, for no capability the Bare-as-working-repo lacks.
- **One repo unifying Mirror + Bare**. Rejected (see ADR-adjacent reasoning):
  a force-prune of upstream refs would delete developer branches unless held to
  permanent ref-namespace discipline; the physical Mirror/Bare split makes that
  a structural guarantee instead.

## Consequences

- Same-host, a developer and a worker share one Bare, so a developer publishes
  work by committing (the branch is already in the Bare) and the worker builds it
  by adding its own detached worktree at the tip (no push). Cross-host uses a
  per-host `<hostname>/<project>` remote (`ssh://`) to the peer's Bare.
- We give up git-level isolation between workbenches: all worktrees hang off one
  Bare, so `git worktree list` spans workbenches and `refs/heads/*` is one global
  branch namespace. Isolation between workbenches is filesystem-only. This matches
  the contention/visibility profile of the prior shared clone, so it is not a
  regression.
