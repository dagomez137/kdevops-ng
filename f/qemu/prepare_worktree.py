# SPDX-License-Identifier: copyleft-next-0.3.1
"""Lay down this worker's warm `main` QEMU worktree, detached at the requested ref.

Thin wrapper over `f.common.worktree.prepare` (the shared worktree logic). The
worktree is cut from the durable Bare at `$SYSTEM_DIR/bare/qemu.git`,
which borrows the local mirror's objects, so checkouts are cheap. Runs `git` on the
host (NOT in the devShell).

The worktree is this worker's `workers/<WORKER_INDEX>/main/qemu`, reused
for every ref and across runs (parallel across workers); apply b4 series over and over.
`recreate_worktree` lays a fresh checkout.

The out-of-tree `build` dir and the `destdir` `--prefix` install target are both
children of the source checkout (`qemu/build`, `qemu/destdir`), so meson emits paths
relative to `build`.

The `destdir` install root is rm+recreated every build: it is per-build staging for
the per-identity install prefixes, and the durable install tree lives in the Store,
not here. Knobs: `wipe_build` rm+recreates the `build` dir first; `b4_series` applies
a lore series on top of the checkout via `b4 shazam` in the devShell.

Equivalent host bash (PATH includes /nix/var/nix/profiles/default/bin):

    git config --global --add safe.directory '*'          # once per container
    git -C "$BARE" fetch --tags --force mirror
    git -C "$BARE" worktree prune
    git -C "$WT" checkout --detach --force "$qemu_ref"   # resolved tag/mirror/literal -> commit
    git -C "$BARE" worktree add --force --detach "$WT" "$qemu_ref"
    git -C "$WT" rev-parse HEAD
"""

from __future__ import annotations

from f.common.worktree import prepare


def main(
    qemu_ref: str = "v11.0.0",
    b4_series: str = "",
    recreate_worktree: bool = False,
    wipe_build: bool = False,
) -> dict:
    qemu_ref = qemu_ref or "v11.0.0"
    wipe_dirs = ("destdir",) + (("build",) if wipe_build else ())
    result = prepare(
        project="qemu",
        developer=False,
        ref=qemu_ref,
        b4_series=b4_series,
        recreate_worktree=recreate_worktree,
        extra_dirs=("build", "destdir"),
        wipe_dirs=wipe_dirs,
        version_file="VERSION",
    )
    result["qemu_ref"] = result["ref"]
    return result
