# SPDX-License-Identifier: copyleft-next-0.3.1
"""Ref listing for the build forms' ref pickers; imported as f.common.gitrefs.

Lists the branches and tags of a host-local bare repository by reading its
`packed-refs` file and loose `refs/` entries directly (no `git` subprocess, so
a form dynselect answers fast). The universe is the durable Bare the worktree
steps check out from (`$SYSTEM_DIR/bare/<repo>.git`), which carries developer
branches plus the mirror-fetched upstream refs; before the first fetch it
falls back to the mirror (`$MIRRORS_DIR/<repo>.git`). Branches come first,
then tags newest version first, so the zero-config pick is the tip of a tree
or the latest release.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_MAX_OPTIONS = 200

_VERSION_RE = re.compile(
    r"^v(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?:-rc(?P<rc>\d+))?$"
)


def _repo_dir(repo: str) -> Path | None:
    system = os.environ.get("SYSTEM_DIR", "")
    mirrors = os.environ.get("MIRRORS_DIR") or (
        str(Path(system) / "mirror") if system else ""
    )
    for root in ([Path(system) / "bare"] if system else []) + (
        [Path(mirrors)] if mirrors else []
    ):
        candidate = root / f"{repo}.git"
        if (candidate / "packed-refs").is_file() or (candidate / "refs").is_dir():
            return candidate
    return None


def _read_refs(bare: Path) -> dict[str, str]:
    """`refs/heads/...`/`refs/tags/...` names -> kind, loose over packed."""
    refs: dict[str, str] = {}
    packed = bare / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(errors="replace").splitlines():
            if not line or line.startswith(("#", "^")):
                continue
            _, _, name = line.partition(" ")
            for kind, prefix in (("head", "refs/heads/"), ("tag", "refs/tags/")):
                if name.startswith(prefix):
                    refs[name[len(prefix) :]] = kind
    for kind, sub in (("head", "heads"), ("tag", "tags")):
        root = bare / "refs" / sub
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file():
                    refs[str(path.relative_to(root))] = kind
    return refs


def _tag_key(name: str) -> tuple:
    """Sort key: released versions newest first, then rcs, then the rest."""
    m = _VERSION_RE.match(name)
    if not m:
        return (1, 0, 0, 0, 0, 0, name)
    rc = m.group("rc")
    return (
        0,
        -int(m.group("major")),
        -int(m.group("minor")),
        -int(m.group("patch") or 0),
        0 if rc is None else 1,
        -int(rc or 0),
        name,
    )


def list_refs(repo: str, filterText: str = "") -> list[dict]:
    """Dynselect options for a repo's refs: branches first, tags newest first.

    Returns an empty list when neither the Bare nor the mirror exists yet; the
    form's manual-ref toggle stays the entry path on such a host.
    """
    bare = _repo_dir(repo)
    if bare is None:
        return []
    refs = _read_refs(bare)
    needle = (filterText or "").lower()
    heads = sorted(n for n, k in refs.items() if k == "head" and needle in n.lower())
    tags = sorted(
        (n for n, k in refs.items() if k == "tag" and needle in n.lower()),
        key=_tag_key,
    )
    return [{"value": n, "label": n} for n in (heads + tags)[:_MAX_OPTIONS]]


def main():
    """Library module imported by the build forms' ref pickers; not a step."""
    return "f/common/gitrefs: bare-repository ref listing"
