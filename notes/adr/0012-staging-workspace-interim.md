# A custom staging workspace, an interim toward Windmill forks

Untested flows should not sit in the production `kdevops` Windmill workspace
until they are exercised and blessed. Windmill offers a native model for this,
workspace forks plus git sync (its Stage 2 to Stage 3), and its new AI
Sessions build directly on forks: a session develops in an isolated fork and
merges back through a review. We do not use that model yet. Instead a second
workspace, `staging`, holds the work in progress, and the `deploy-kdevops` app
prunes a staging-only set from the tree it pushes, so production carries only
promoted work.

This record states that the custom model is a deliberate interim, and why the
native model is deferred rather than adopted now.

The load-bearing fact is that we are not locked in. The git repository
(`wmill.yaml` plus `f/`, driven by `wmill sync push`) is the exact substrate
Windmill's git sync consumes, so enabling git sync later sits on the same
repository. The custom layer is thin: the `staging` workspace declaration, the
two deploy apps, and the prune list, all reversible. The one real cost is that
Community Edition allows two workspaces outside the built-in `admins`, and
`kdevops` plus `staging` already use both; a fork needs a slot, so the
`staging` workspace is exactly the slot a fork would occupy. Freeing it is one
workspace deletion.

Three facts make the native model premature rather than wrong. Our Windmill
fork is 1.741 and carries no AI Sessions code; Sessions landed upstream after
it, so adopting them needs a version bump and a Community Edition availability
check first. Forks do exist in 1.741 and work in Community Edition, but only
one at a time within the two-workspace limit, so running several sessions in
parallel, each in its own fork, is an Enterprise feature. And git sync is
bi-directional where our doctrine is git-is-truth and push-only, while Sessions
develop inside the instance; both shift the center of gravity from the
repository toward the instance, a change worth deciding on its own.

## Status

accepted (interim)

## Considered Options

- **Windmill forks plus git sync now (Stage 2 to Stage 3).** The canonical
  model and the eventual target: one `kdevops` workspace, a fork per feature or
  session, merged back through the merge UI or a git PR, git sync keeping the
  repository in the loop. Deferred, not rejected: Sessions are absent from our
  version, Community Edition caps concurrent forks at one, and the
  bi-directional, instance-first shift wants its own decision.
- **Windmill multi-workspace promotion (Stage 4).** Separate `staging` and
  `prod` workspaces, each bound to its own git branch and promoted by a git PR.
  Rejected: Windmill documents it as operational overhead reserved for a
  separate production environment, and it is Enterprise or Cloud only.
- **A manual `staging` git branch merged to `main`.** Rejected: it is a third
  custom variant rather than Windmill's model, forks already are the
  branch-per-change mechanism, and a long-lived non-main branch trips the
  reflow tooling into adding a junk `wmill.yaml` workspace entry.
- **The custom `staging` workspace with a prune gate (chosen).** Works in
  Community Edition today, keeps git-is-truth and push-only, and gates untested
  work out of production. Its cost is the consumed fork slot and the divergence
  from Windmill's model, both accepted as interim.

## Migration

Adopt the native model when Sessions are wanted and available in a
Community-Edition-compatible version: bump the Windmill fork, confirm Sessions
and the fork count fit Community Edition (or plan for Enterprise if several
parallel sessions are needed), delete the `staging` workspace to free the slot,
enable git sync on `kdevops`, and move work-in-progress isolation from the
prune gate to forks. The `deploy-staging` and `deploy-kdevops` apps and the
prune list retire at that point.
