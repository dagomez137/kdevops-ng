# SPDX-License-Identifier: copyleft-next-0.3.1
"""Resolve the closure's source overrides before rendering.

An override with a `b4_series` gets the kernel-build treatment through
`f.common.worktree.prepare`: the project's worker worktree is laid at the
ref, the mailed series is applied with `b4 am`, and the result is published
to the Bare as `b4/<slug>`, created once and never moved; the override's
effective ref becomes that branch, or the applied commit when the branch
already sits elsewhere, so the flake input clones exactly what was applied. The
one developer group a deploy tail uses is derived here too: a series names
it after its cover subject, an upstream tag keeps `vanilla`, a full commit
id its short sha, and any other ref its own slug; differing derivations
warn and the first wins.

Equivalent bash, per seriesed override:

    git -C "$WORKERS_DIR/$WORKER_INDEX/main/<project>" checkout --detach --force <ref>
    b4 --config b4.midmask=https://lore.kernel.org/all/%s am --outdir <tmp> <series>
    git -c user.name=kdevops -c user.email=kdevops@kdevops am \\
        --committer-date-is-author-date <tmp>/*.mbx
    git update-ref refs/heads/b4/<slug> HEAD ""   # create only
    git update-ref refs/kdevops/b4/<slug> HEAD     # else: anchor this run's sha
"""

from __future__ import annotations

from f.common.gitrefs import qualify_ref
from f.common.worktree import _slug, prepare
from f.nix.render_config import _FULL_SHA_RE, _OVERRIDABLE_PKGS, _PKG_PROJECTS


def _auto_group(project: str, ref: str) -> str:
    """The override's auto group: `vanilla` for a tag, else named after the ref."""
    if _FULL_SHA_RE.match(ref):
        return _slug(ref[:12])
    qualified = qualify_ref(project, ref)
    if not qualified:
        raise ValueError(
            f"ref {ref!r} not found in the {project} Bare (tried refs/tags, "
            "refs/remotes/mirror, refs/heads, refs/remotes); push the branch "
            "to the Bare or refresh the mirror (f/workbench/fetch)"
        )
    return "vanilla" if qualified.startswith("refs/tags/") else _slug(ref)


def main(source_overrides: dict | None = None) -> dict:
    rows: list[dict] = []
    groups: list[str] = []
    for pkg in _OVERRIDABLE_PKGS:
        spec = (source_overrides or {}).get(pkg) or {}
        ref = str(spec.get("ref", "") or "").strip()
        series = str(spec.get("b4_series", "") or "").strip()
        if not ref and not series:
            continue
        if not ref:
            raise ValueError(f"override {pkg!r}: b4_series needs a ref to apply onto")
        project = _PKG_PROJECTS[pkg]
        if series:
            prepared = prepare(project=project, ref=ref, b4_series=series)
            ref = prepared.get("b4_branch") or prepared["commit"]
            group = prepared["label"]
        else:
            group = _auto_group(project, ref)
        rows.append({"pkg": pkg, "project": project, "ref": ref})
        groups.append(group)

    worktree_group = groups[0] if groups else ""
    distinct = sorted(set(groups))
    if len(distinct) > 1:
        print(
            f"warning: override worktree groups differ ({', '.join(distinct)}); "
            f"a deploy uses the first ({worktree_group})",
            flush=True,
        )
    return {
        "source_overrides": {row["pkg"]: {"ref": row["ref"]} for row in rows},
        "overrides": rows,
        "worktree_group": worktree_group,
    }
