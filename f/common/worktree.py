# SPDX-License-Identifier: copyleft-next-0.3.1
"""Shared worktree-prepare helper for the kdevops-ng build steps (not a runnable step).

Imported with:  from f.common.worktree import prepare

`prepare()` lays down a detached worktree of a shared bare-mirror clone
(`workers/shared/<project>/...`) inside a per-project shared workspace tree
(`workers/shared/ws/<project>/<name>`), so checkouts are cheap and every worker
sees the same trees. It runs `git` on the host (NOT in the devShell); only the
optional `b4 shazam` step crosses into the `nixos-flake#build` devShell.

Two modes (the `shared` flag):

- `shared=False` (default) -> `workers/<WORKER_INDEX>/<project>`. One tree per
  worker, reused for every ref and across runs; apply b4 series on top over and
  over. Because each worker has its own tree, isolated builds on different workers
  run in parallel without contending. The everyday build / iterative dev tree.
- `shared=True` -> `workers/shared/ws/<project>/<name>`, where <name> is the user
  string if given, else a slug of the b4 series, else the flow job id. A shared,
  persistent named tree any worker can pick up (e.g. one per patch series).

`reuse_worktree=True` skips all git orchestration (fetch, checkout, b4) and builds
the named tree exactly as the operator left it checked out — for iterating on a local
branch without prepare resetting HEAD. The tree must already exist.

Equivalent host bash (PATH includes /nix/var/nix/profiles/default/bin):

    git config --global --add safe.directory '*'          # once per container
    # refresh refs from the LOCAL mirror (the clone's first object alternate), not
    # origin's upstream URL — the worker reaches the mirror, maybe not git.kernel.org.
    # Fetch into refs/remotes/mirror/* (NOT refs/heads/*) so it never deletes or
    # overwrites an operator's local branch; no --prune, no HEAD detach needed.
    MIRROR=$(sed 's,/objects$,,' "$MAIN/.git/objects/info/alternates" | head -1)
    git -C "$MAIN" fetch --tags --force "$MIRROR" '+refs/heads/*:refs/remotes/mirror/*'
    # resolve to a commit (tag, else mirror/<ref>, else literal) and detach onto it:
    TARGET=$(git -C "$MAIN" rev-parse --verify "refs/tags/$ref^{commit}" 2>/dev/null \
             || git -C "$MAIN" rev-parse --verify "mirror/$ref^{commit}" 2>/dev/null \
             || git -C "$MAIN" rev-parse --verify "$ref^{commit}")
    git -C "$MAIN" worktree prune
    git -C "$WT" checkout --detach --force "$TARGET"
    git -C "$MAIN" worktree add --force --detach "$WT" "$TARGET"   # if not a checkout yet
    git -C "$WT" config user.name kdevops                  # b4 shazam's git am needs a committer
    git -C "$WT" config user.email kdevops@kdevops
    b4 shazam "$b4_series"                                 # optional, in the devShell, cwd=$WT
    git -C "$WT" rev-parse HEAD
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from f.common.devshell import DevShell, Git


def main():
    """This module is a library imported by the build steps, not a runnable step."""
    return "f/common/worktree: shared worktree-prepare helper"


def prepare(
    *,
    project: str,
    main_repo_subpath: str,
    worktree_dirname: str,
    ref: str,
    shared: bool = False,
    workspace: str = "",
    b4_series: str = "",
    reuse_worktree: bool = False,
    extra_dirs: tuple = (),
    wipe_dirs: tuple = (),
    version_file: str = "",
) -> dict:
    if ref.startswith("-"):
        raise ValueError(f"invalid ref: {ref}")

    job_id = os.environ.get("WM_ROOT_FLOW_JOB_ID") or os.environ.get("WM_JOB_ID") or "adhoc"
    name = _custom_name(workspace, b4_series, job_id) if shared else "default"
    if shared and (not name or "/" in name or ".." in name):
        raise ValueError(f"invalid worktree name: {name!r}")

    git = Git()
    existing = git.capture("config", "--global", "--get-all", "safe.directory", check=False)
    if "*" not in existing.split("\n"):
        git.run("config", "--global", "--add", "safe.directory", "*")

    workers = Path(os.environ["WORKERS_DIR"])
    worker_index = os.environ["WORKER_INDEX"]
    main_repo = workers / main_repo_subpath

    if not (main_repo / ".git").exists() and not (main_repo / "objects").is_dir():
        raise FileNotFoundError(
            f"shared {project} main repo missing at {main_repo} — run f/workspace/init first")
    if not (workers / "shared/nixos-flake/flake.nix").exists():
        raise FileNotFoundError(
            f"nixos-flake devShell missing at {workers / 'shared/nixos-flake'} "
            "— provision it first")

    if shared:
        slot = workers / "shared/ws" / project / name
    else:
        slot = workers / worker_index / project
    worktree = slot / worktree_dirname
    build_dir = worktree / "build"
    print(f"worker={worker_index} ref={ref} shared={shared} name={name} "
          f"worktree={worktree}", flush=True)

    slot.mkdir(parents=True, exist_ok=True)
    for d in extra_dirs:
        if d == "build":
            continue
        if d in wipe_dirs:
            shutil.rmtree(slot / d, ignore_errors=True)
        (slot / d).mkdir(parents=True, exist_ok=True)

    if reuse_worktree:
        # Operator-owned mode: build the tree exactly as checked out — no fetch, no
        # checkout, no b4 (ref/b4_series are ignored). Skipping the fetch is also what
        # protects a local working branch, which the prune would otherwise delete. The
        # named tree must already exist (lay it down once without reuse first).
        if not git.ok("-C", str(worktree), "rev-parse", "--git-dir"):
            raise FileNotFoundError(
                f"reuse_worktree set but no worktree at {worktree} — run once without "
                "reuse to lay it down, then reuse it")
        print(f"reuse: building {worktree} at its current HEAD (no fetch/checkout/b4)",
              flush=True)
    else:
        fetch_src = _mirror_of(main_repo) or "origin"
        # Fetch the mirror's branches into a private remotes namespace, never into
        # refs/heads/* where operators keep local branches — so a fetch can neither
        # delete (no --prune) nor force-overwrite a working branch. Tags arrive via
        # --tags; fetching into refs/remotes/* is never refused, so no HEAD detach.
        if not git.ok("-C", str(main_repo), "fetch", "--tags", "--force", fetch_src,
                      "+refs/heads/*:refs/remotes/mirror/*"):
            print(f"note: fetch of shared {project} from {fetch_src} failed; using local refs",
                  flush=True)
        target = _resolve_ref(git, main_repo, ref)
        git.run("-C", str(main_repo), "worktree", "prune")
        if git.ok("-C", str(worktree), "rev-parse", "--git-dir"):
            git.run("-C", str(worktree), "checkout", "--detach", "--force", target)
        else:
            shutil.rmtree(worktree, ignore_errors=True)
            git.run("-C", str(main_repo), "worktree", "add", "--force", "--detach",
                    str(worktree), target)
        if b4_series:
            # b4 shazam's `git am` needs a committer identity the worker container lacks.
            git.run("-C", str(worktree), "config", "user.name", "kdevops")
            git.run("-C", str(worktree), "config", "user.email", "kdevops@kdevops")
            DevShell(workers).run("b4", "shazam", b4_series, cwd=str(worktree))

    # build/ lives under the worktree, created after it is laid down.
    if "build" in extra_dirs:
        if "build" in wipe_dirs:
            shutil.rmtree(build_dir, ignore_errors=True)
        build_dir.mkdir(parents=True, exist_ok=True)
        _exclude_build(main_repo)

    commit = git.capture("-C", str(worktree), "rev-parse", "HEAD").strip()
    if reuse_worktree:
        # ref is ignored in reuse mode; report what is actually checked out (the branch
        # name, or the short commit when HEAD is detached) so result["ref"] — and the
        # manifests derived from it — describe what was really built.
        head = git.capture("-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD").strip()
        ref = head if head and head != "HEAD" else commit[:12]
    _list_dir(worktree)

    result = {
        "worker": worker_index,
        "ref": ref,
        "commit": commit,
        "slot": str(slot),
        "worktree": str(worktree),
        "shared": shared,
        "name": name,
        "b4_series": b4_series or None,
    }
    if "build" in extra_dirs:
        result["build_dir"] = str(build_dir)
    if "destdir" in extra_dirs:
        result["destdir"] = str(slot / "destdir")
    if version_file:
        result["version"] = _read_version(worktree, version_file)
    return result


def _resolve_ref(git: Git, main_repo: Path, ref: str) -> str:
    """Resolve `ref` to a commit SHA: a tag first, then the mirror remote, then the
    literal ref (a commit, or an operator's local branch in refs/heads/*).

    The worktree is always laid down detached, so a concrete commit is all the
    checkout/worktree-add needs — and resolving the mirror's branches via
    `refs/remotes/mirror/*` keeps them out of refs/heads/*, so the fetch can never
    touch a local working branch (a tag like `v11.0.0` still wins outright).
    """
    for candidate in (f"refs/tags/{ref}", f"mirror/{ref}", ref):
        sha = git.capture("-C", str(main_repo), "rev-parse", "--verify", "--quiet",
                          f"{candidate}^{{commit}}", check=False).strip()
        if sha:
            return sha
    raise ValueError(
        f"could not resolve ref {ref!r} in {main_repo} "
        "(tried a tag, the mirror remote, and the literal ref)")


def _exclude_build(main_repo: Path) -> None:
    """Ignore the worktree-local `build/` via the clone's shared exclude (all worktrees)."""
    gitdir = main_repo / ".git" if (main_repo / ".git").is_dir() else main_repo
    info = gitdir / "info"
    exclude = info / "exclude"
    if exclude.is_file() and "/build/" in exclude.read_text().splitlines():
        return
    info.mkdir(parents=True, exist_ok=True)
    with exclude.open("a") as handle:
        handle.write("/build/\n")


def _mirror_of(main_repo: Path) -> str | None:
    """The local bare mirror backing this clone (its first object alternate).

    Refs are fetched from this local mirror — reliable and fresh (the host keeps it
    current) — rather than from `origin`'s upstream URL, which a worker may not reach.
    """
    alternates = main_repo / ".git" / "objects" / "info" / "alternates"
    if not alternates.is_file():
        return None
    for line in alternates.read_text().splitlines():
        line = line.strip()
        if line.endswith("/objects"):
            return line[: -len("/objects")]
    return None


def _custom_name(workspace: str, b4_series: str, job_id: str) -> str:
    """Resolve a custom worktree name: the user string, else the b4 slug, else the job id."""
    if workspace:
        return _slug(workspace)
    if b4_series:
        slug = _slug(b4_series)
        if slug:
            return slug
    return job_id


def _slug(value: str) -> str:
    """Reduce a name/URL/message-id to a filesystem-safe path component (<=48 chars)."""
    value = value.strip().strip("/")
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    value = value.split("@", 1)[0]
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._").lower()
    return value[:48]


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
