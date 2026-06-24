# SPDX-License-Identifier: copyleft-next-0.3.1
"""Initialize a developer worktree-group: one worktree per project, in one call.

Runnable step. Given a worktree-group name and a list of projects (each a `project`
plus a `git_ref` and an optional `b4_series`), it cuts a developer worktree per
project under `<workbench>/<worktree-group>/<project>` off that project's durable
Bare, through `f.common.worktree.prepare` with `developer=True`. A developer uses
this to let Windmill stand up a whole topic group at once (e.g. `largeio` with
`linux` at one ref, `qemu` at another, `xfsprogs-dev` at a third). `system` and
`workers` are reserved group names. Per project it is idempotent: an existing
worktree is reused unless `recreate_worktree` is set. The worker bind-mounts the
whole Workbench, so the group lands host-visibly where the developer edits it.
"""

from __future__ import annotations

from f.common.worktree import prepare, validate_group


def main(worktree_group: str, projects: list[dict] | None = None,
         recreate_worktree: bool = False) -> dict:
    # Validate the group before touching any project, so a reserved/malformed group
    # fails fast rather than after some worktrees are already laid down.
    validate_group(worktree_group)
    entries = [e for e in (projects or []) if e and e.get("project")]
    if not entries:
        raise ValueError("projects must list at least one {project, git_ref}")

    worktrees = []
    for entry in entries:
        project = entry["project"]
        ref = entry.get("git_ref") or entry.get("ref")
        if not ref:
            raise ValueError(f"project {project!r}: a git_ref is required")
        result = prepare(project=project, ref=ref, worktree_group=worktree_group,
                         developer=True, b4_series=entry.get("b4_series") or "",
                         recreate_worktree=recreate_worktree)
        worktrees.append({
            "project": result["project"],
            "ref": result["ref"],
            "commit": result["commit"],
            "worktree": result["worktree"],
            "b4_branch": result["b4_branch"],
        })

    print(f"worktree-group {worktree_group}: {len(worktrees)} worktree(s) ready", flush=True)
    return {"worktree_group": worktree_group, "worktrees": worktrees}
