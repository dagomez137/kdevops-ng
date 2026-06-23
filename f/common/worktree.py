# SPDX-License-Identifier: copyleft-next-0.3.1
"""Shared worktree-prepare helper for the kdevops-ng build steps (not a runnable step).

Imported with:  from f.common.worktree import prepare

`prepare()` lays down one warm, detached `main` worktree per (worker, namespace)
off the durable Bare at `workers/system/bare/<namespace>/<canonical>.git` (see
`f/workspace/fetch.py`). The Bare borrows the local mirror's objects, so cutting a
worktree is cheap and every worker sees the same trees. It runs `git` on the host
(NOT in the devShell); only the optional `b4 shazam` step crosses into the
`nixos-flake#build` devShell.

The slot is `workers/<WORKER_INDEX>/<namespace>/main`; the worktree is
`<slot>/<canonical>`, reused for every ref and across runs. `build` and `destdir`
are children of the worktree. `recreate_worktree=True` rm's the worktree and lays a
fresh detached checkout.

Equivalent host bash (PATH includes /nix/var/nix/profiles/default/bin):

    git config --global --add safe.directory '*'          # once per container
    # refresh upstream refs into the Bare's refs/remotes/mirror/* (developer
    # branches already live in the Bare's refs/heads/* on the same host):
    git -C "$BARE" fetch --tags --force mirror
    # resolve to a commit (tag, else mirror/<ref>, else literal) and detach onto it:
    TARGET=$(git -C "$BARE" rev-parse --verify "refs/tags/$ref^{commit}" 2>/dev/null \
             || git -C "$BARE" rev-parse --verify "mirror/$ref^{commit}" 2>/dev/null \
             || git -C "$BARE" rev-parse --verify "$ref^{commit}")
    git -C "$BARE" worktree prune
    git -C "$WT" checkout --detach --force "$TARGET"
    git -C "$BARE" worktree add --force --detach "$WT" "$TARGET"   # if not a checkout yet
    git -C "$WT" config user.name kdevops                  # b4 shazam's git am needs a committer
    git -C "$WT" config user.email kdevops@kdevops
    b4 shazam "$b4_series"                                 # optional, in the devShell, cwd=$WT
    git -C "$WT" rev-parse HEAD
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from f.common.devshell import DevShell, Git


def main():
    """This module is a library imported by the build steps, not a runnable step."""
    return "f/common/worktree: shared worktree-prepare helper"


def prepare(
    *,
    namespace: str,
    canonical: str,
    ref: str,
    b4_series: str = "",
    recreate_worktree: bool = False,
    extra_dirs: tuple = (),
    wipe_dirs: tuple = (),
    version_file: str = "",
) -> dict:
    if ref.startswith("-"):
        raise ValueError(f"invalid ref: {ref}")

    git = Git()
    existing = git.capture("config", "--global", "--get-all", "safe.directory", check=False)
    if "*" not in existing.split("\n"):
        git.run("config", "--global", "--add", "safe.directory", "*")

    workers = Path(os.environ["WORKERS_DIR"])
    worker_index = os.environ["WORKER_INDEX"]
    bare = workers / "system" / "bare" / namespace / f"{canonical}.git"
    slot = workers / worker_index / namespace / "main"
    worktree = slot / canonical
    build_dir = worktree / "build"

    if not (bare / "objects").is_dir():
        raise FileNotFoundError(f"Bare {bare} missing — run f/workspace/init first")
    if not (workers / "shared/nixos-flake/flake.nix").exists():
        raise FileNotFoundError(
            f"nixos-flake devShell missing at {workers / 'shared/nixos-flake'} "
            "— provision it first")

    print(f"worker={worker_index} ref={ref} worktree={worktree}", flush=True)

    slot.mkdir(parents=True, exist_ok=True)

    # Only upstream refs need a fetch; developer branches are already in the Bare's
    # refs/heads/* on the same host. A failed fetch is non-fatal — fall back to local refs.
    if not git.ok("-C", str(bare), "fetch", "--tags", "--force", "mirror"):
        print(f"note: fetch of {bare} from mirror failed; using local refs", flush=True)
    target = _resolve_ref(git, bare, ref)
    git.run("-C", str(bare), "worktree", "prune")
    if recreate_worktree:
        shutil.rmtree(worktree, ignore_errors=True)
        git.run("-C", str(bare), "worktree", "prune")
    if git.ok("-C", str(worktree), "rev-parse", "--git-dir"):
        git.run("-C", str(worktree), "checkout", "--detach", "--force", target)
    else:
        shutil.rmtree(worktree, ignore_errors=True)
        git.run("-C", str(bare), "worktree", "add", "--force", "--detach",
                str(worktree), target)
    if b4_series:
        # b4 shazam's `git am` needs a committer identity the worker container lacks.
        git.run("-C", str(worktree), "config", "user.name", "kdevops")
        git.run("-C", str(worktree), "config", "user.email", "kdevops@kdevops")
        DevShell(workers).run("b4", "shazam", b4_series, cwd=str(worktree))

    for d in extra_dirs:
        target_dir = worktree / d
        if d in wipe_dirs:
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)
    _exclude_dirs(bare, extra_dirs)

    commit = git.capture("-C", str(worktree), "rev-parse", "HEAD").strip()
    _list_dir(worktree)

    result = {
        "worker": worker_index,
        "namespace": namespace,
        "canonical": canonical,
        "ref": ref,
        "commit": commit,
        "slot": str(slot),
        "worktree": str(worktree),
        "b4_series": b4_series or None,
    }
    if "build" in extra_dirs:
        result["build_dir"] = str(build_dir)
    if "destdir" in extra_dirs:
        result["destdir"] = str(worktree / "destdir")
    if version_file:
        result["version"] = _read_version(worktree, version_file)
    return result


def _resolve_ref(git: Git, bare: Path, ref: str) -> str:
    """Resolve `ref` to a commit SHA: a tag first, then the mirror remote, then the
    literal ref (a commit, or a developer branch in refs/heads/*).

    The worktree is always laid down detached, so a concrete commit is all the
    checkout/worktree-add needs — and resolving the mirror's branches via
    `refs/remotes/mirror/*` keeps them out of refs/heads/*, where developer pushes
    live (a tag like `v11.0.0` still wins outright).
    """
    for candidate in (f"refs/tags/{ref}", f"mirror/{ref}", ref):
        sha = git.capture("-C", str(bare), "rev-parse", "--verify", "--quiet",
                          f"{candidate}^{{commit}}", check=False).strip()
        if sha:
            return sha
    raise ValueError(
        f"could not resolve ref {ref!r} in {bare} "
        "(tried a tag, the mirror remote, and the literal ref)")


def _exclude_dirs(bare: Path, extra_dirs: tuple) -> None:
    """Ignore each worktree-local extra dir via the Bare's shared exclude (all worktrees)."""
    gitdir = bare / ".git" if (bare / ".git").is_dir() else bare
    info = gitdir / "info"
    exclude = info / "exclude"
    present = exclude.read_text().splitlines() if exclude.is_file() else []
    missing = [f"/{d}/" for d in extra_dirs if f"/{d}/" not in present]
    if not missing:
        return
    info.mkdir(parents=True, exist_ok=True)
    with exclude.open("a") as handle:
        handle.write("".join(line + "\n" for line in missing))


def _read_version(worktree: Path, version_file: str) -> str | None:
    """Read a version string from `<worktree>/<version_file>` (absent -> None)."""
    path = worktree / version_file
    if not path.is_file():
        return None
    return path.read_text().strip() or None


def _list_dir(path: Path) -> None:
    """Log the worktree directory entry."""
    info = path.stat()
    print(f"{path}  (mode {info.st_mode & 0o777:o})", flush=True)
