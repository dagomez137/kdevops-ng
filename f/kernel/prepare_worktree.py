# SPDX-License-Identifier: copyleft-next-0.3.1
"""Resolve a build slot and lay down a detached worktree of the shared kernel mirror.

Thin wrapper over `f.common.worktree.prepare` (the shared slot/worktree logic). The
worktree shares objects with `workers/shared/kernel/linux` (cloned from the bare
mirror), so checkouts are cheap. Runs `git` on the host (NOT in the devShell).

- `shared=False` (default) reuses this worker's own tree
  `workers/<WORKER_INDEX>/kernel` for every ref (parallel across workers); apply b4
  series over and over.
- `shared=True` lays down a shared, persistent named tree
  `workers/shared/ws/kernel/<name>`, where <name> is `workspace` if given, else a
  slug of `b4_series`, else the flow job id.
- `reuse_worktree=True` skips fetch/checkout/b4 and builds the named tree exactly as
  checked out (iterate on a local branch); `git_ref`/`b4_series` are ignored.

The out-of-tree `build` dir lives under the source checkout (`linux/build`), so kbuild
emits paths relative to it; `destdir` stays a slot-level sibling of the source.

Knobs: `wipe_build` rm+recreates the `build` dir first; `clean_destdir` (default
false) rm+recreates the `destdir` install sibling first — leave it off so an install
never wipes modules a running QEMU/systemd VM has mounted over virtiofs; `b4_series`
applies a lore series on top of the checkout via `b4 shazam` in the devShell.

Equivalent host bash (PATH includes /nix/var/nix/profiles/default/bin):

    git config --global --add safe.directory '*'          # once per container
    git -C "$MAIN" fetch --tags --force origin '+refs/heads/*:refs/remotes/mirror/*'  # not refs/heads/*: keeps local branches
    git -C "$MAIN" worktree prune
    git -C "$WT" checkout --detach --force "$git_ref"
    git -C "$MAIN" worktree add --force --detach "$WT" "$git_ref"
    git -C "$WT" rev-parse HEAD
"""

from __future__ import annotations

from f.common.worktree import prepare


def main(git_ref: str = "v7.1-rc7", shared: bool = False, workspace: str = "",
         b4_series: str = "", reuse_worktree: bool = False, wipe_build: bool = False,
         clean_destdir: bool = False) -> dict:
    git_ref = git_ref or "v7.1-rc7"
    wipe_dirs = (("build",) if wipe_build else ()) + (("destdir",) if clean_destdir else ())
    result = prepare(
        project="kernel",
        main_repo_subpath="shared/kernel/linux",
        worktree_dirname="linux",
        ref=git_ref,
        shared=shared,
        workspace=workspace,
        b4_series=b4_series,
        reuse_worktree=reuse_worktree,
        extra_dirs=("build", "destdir"),
        wipe_dirs=wipe_dirs,
    )
    # result["ref"] echoes the input ref, except in reuse mode where it reports the
    # actually-checked-out branch/commit — mirror that so the manifest is honest.
    result["git_ref"] = result["ref"]
    return result
