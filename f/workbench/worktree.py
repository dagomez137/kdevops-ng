# SPDX-License-Identifier: copyleft-next-0.3.1
"""Create or refresh a developer's own worktree of a project within a worktree-group.

Runnable step. Thin wrapper over `f.common.worktree.prepare` with `developer=True`.
The worktree lands at `<workbench>/<worktree-group>/<project>` (default group
`vanilla`), a plain detached checkout cut from the durable Bare at
`$SYSTEM_DIR/bare/<project>.git`. The Bare borrows the local mirror's objects, so the
checkout is cheap and shares the same object graph the workers build from. `system`
and `workers` are reserved group names. A developer worktree is a plain checkout (no
out-of-tree `build` or `destdir`); reuse it across refs, or pass `recreate_worktree`
to lay a fresh one. `b4_series` applies a lore series on top via `b4 shazam`.
"""

from __future__ import annotations

from f.common.worktree import prepare


def main(project: str, git_ref: str, worktree_group: str = "vanilla",
         b4_series: str = "", recreate_worktree: bool = False) -> dict:
    return prepare(project=project, ref=git_ref, worktree_group=worktree_group,
                   developer=True, b4_series=b4_series,
                   recreate_worktree=recreate_worktree)
