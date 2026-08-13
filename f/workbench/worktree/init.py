# SPDX-License-Identifier: copyleft-next-0.3.1
"""Initialize a developer worktree-group: one worktree per project, in one call.

Runnable step. Given a worktree-group name and a list of projects (each a `project`
plus a `git_ref` and an optional `b4_series`), it cuts a developer worktree per
project under `<workbench>/<worktree-group>/<project>` off that project's durable
Bare, through `f.common.worktree.prepare_developer`. A developer uses this to let
Windmill stand up a whole topic group at once (e.g. `largeio` with `linux` at one ref,
`qemu` at another, `xfsprogs-dev` at a third). An entry may carry its own
`worktree_group`, overriding the shared name: a build tail uses that to give each
project its auto-derived group while a custom name still gathers them into one.
`system` and `workers` are reserved group names. Per project it is idempotent:
an existing worktree is reused unless `recreate_worktree` is set. The worker
bind-mounts the whole Workbench, so the group lands host-visibly where the
developer edits it.

Syncing a project is best-effort and never discards work. A tree the step cannot move
onto the ref without reverting a modified file, dropping a staged one, or moving a
branch the developer owns is left exactly as it is, with `synced: false` and the one
`reason` it was skipped; the step still returns normally, so a build flow's tail does
not throw away a build that already succeeded over a dirty tree.
"""

from __future__ import annotations

from f.common.worktree import prepare_developer, validate_group


def main(
    worktree_group: str = "",
    projects: list[dict] | None = None,
    recreate_worktree: bool = False,
) -> dict:
    entries = [e for e in (projects or []) if e and e.get("project")]
    if not entries:
        raise ValueError("projects must list at least one {project, git_ref}")

    # Resolve and validate every group before touching any project, so a
    # reserved/malformed name fails fast rather than after some worktrees are
    # already laid down.
    groups = []
    for entry in entries:
        group = entry.get("worktree_group") or worktree_group
        if not group:
            raise ValueError(f"project {entry['project']!r}: no worktree_group given")
        validate_group(group)
        groups.append(group)

    worktrees = []
    for entry, group in zip(entries, groups, strict=True):
        project = entry["project"]
        ref = entry.get("git_ref") or entry.get("ref")
        if not ref:
            raise ValueError(f"project {project!r}: a git_ref is required")
        result = prepare_developer(
            project=project,
            ref=ref,
            worktree_group=group,
            b4_series=entry.get("b4_series") or "",
            recreate_worktree=recreate_worktree,
        )
        worktrees.append(
            {
                "project": result["project"],
                "worktree_group": group,
                "ref": result["ref"],
                "commit": result["commit"],
                "worktree": result["worktree"],
                "b4_branch": result["b4_branch"],
                "synced": result["synced"],
                "reason": result["reason"],
            }
        )

    synced = sum(1 for w in worktrees if w["synced"])
    names = ", ".join(sorted(set(groups)))
    print(
        f"worktree-group(s) {names}: {len(worktrees)} worktree(s), {synced} synced",
        flush=True,
    )
    return {"worktree_group": worktree_group, "worktrees": worktrees}
