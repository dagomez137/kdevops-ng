# SPDX-License-Identifier: copyleft-next-0.3.1
"""Lay down this worker's warm `main` kernel worktree, detached at the requested ref.

Thin wrapper over `f.common.worktree.prepare` (the shared worktree logic). The
worktree is cut from the durable Bare at `$SYSTEM_DIR/bare/linux.git`,
which borrows the local mirror's objects, so checkouts are cheap. Runs `git` on the
host (NOT in the devShell).

The worktree is this worker's `workers/<WORKER_INDEX>/main/linux`, reused for
every ref and across runs (parallel across workers); apply b4 series over and over.
`recreate_build_worktree` lays a fresh checkout.

The out-of-tree `build` dir and the `destdir` install target are both children of the
source checkout (`linux/build`, `linux/destdir`), so kbuild emits paths relative to
`build`.

The `destdir` install dir is rm+recreated every build: it is per-build staging for
the install steps, and the durable run layer lives in the Store, not here. Knobs:
`wipe_build` rm+recreates the `build` dir first; `b4_series` applies a lore series
on top of the checkout via `b4 shazam` in the devShell.

Equivalent host bash (PATH includes /nix/var/nix/profiles/default/bin):

    git config --global --add safe.directory '*'          # once per container
    git -C "$BARE" fetch --tags --force mirror
    git -C "$BARE" worktree prune
    git -C "$WT" checkout --detach --force "$git_ref"
    git -C "$BARE" worktree add --force --detach "$WT" "$git_ref"
    git -C "$WT" rev-parse HEAD
"""

from __future__ import annotations

from f.common.worktree import prepare


def main(
    git_ref: str = "v7.1-rc7",
    b4_series: str = "",
    recreate_build_worktree: bool = False,
    wipe_build: bool = False,
) -> dict:
    git_ref = git_ref or "v7.1-rc7"
    wipe_dirs = ("destdir",) + (("build",) if wipe_build else ())
    result = prepare(
        project="linux",
        developer=False,
        ref=git_ref,
        b4_series=b4_series,
        recreate_worktree=recreate_build_worktree,
        extra_dirs=("build", "destdir"),
        wipe_dirs=wipe_dirs,
    )
    result["git_ref"] = result["ref"]
    return result
