# SPDX-License-Identifier: copyleft-next-0.3.1
"""Clone/refresh the shared no-checkout main repos every build worktree is cut from.

Runnable step. For each entry it ensures `WORKERS_DIR/<subpath>` exists as a
`--no-checkout` clone built **from the local bare mirror** (`--shared`), borrowing the
extra trees' objects via `--reference-if-able`, so the clone is fast (no objects
copied) and needs no upstream network. `origin` is then re-pointed at the preferred
upstream URL purely so a human `git fetch origin` works; everything automated fetches
refs from the local mirrors, which the worker can always reach (`git.kernel.org` is
often unreachable). The per-worker worktrees `f/common/worktree.prepare` adds on top
are cheap because they share the same object store. Idempotent.

The upstream candidates come from the entry's `upstreams` list (user order), else its
back-compat `upstream` string, else are derived from the mirror's `remote.origin.url`
(https preferred, googlesource next, the raw `git://` last). The clone's HEAD is
detached so a fetch into `refs/heads/*` is not refused.

An entry may carry extra `remotes`, each `{name, mirror, upstream?/upstreams?}`: a git
remote whose URL is its own preferred upstream but whose refs are fetched from its own
local mirror, and whose objects are borrowed via the repo-wide alternates file (not a
per-remote flag — `git remote add` / `git fetch` take no `--reference`), wired as a
clone-time `--reference-if-able` and reconciled into `.git/objects/info/alternates` to
cover preexisting clones made without it.

Equivalent host bash (PATH includes /nix/var/nix/profiles/default/bin), per mirror:

    git config --global --add safe.directory '*'        # once per container
    mkdir --parents "$(dirname "$shared")"
    # offline clone: refs+objects from the local mirror, extra objects borrowed too:
    git clone --no-checkout --shared "$mirror" \
        --reference-if-able /mirror/linux-next.git \
        --reference-if-able /mirror/linux-stable.git \
        --reference-if-able /mirror/linux-modules.git "$shared"
    # existing clones made without the extra references: borrow their objects too:
    printf '%s\n' /mirror/linux-next.git/objects >> "$shared/.git/objects/info/alternates"
    # detach HEAD so the fetch into refs/heads/* is not refused:
    git -C "$shared" update-ref --no-deref HEAD "$(git -C "$shared" rev-parse HEAD)"
    # $preferred is the first of the per-entry upstream list (https preferred; git://
    # is often blocked); used only for an explicit human `git fetch origin`:
    git -C "$shared" remote set-url origin "$preferred"
    # refresh refs from the LOCAL mirror (reliable; the host keeps it fresh):
    git -C "$shared" fetch --tags --force --prune "$mirror" '+refs/heads/*:refs/heads/*'
    # add each extra remote (URL = its preferred upstream) and fetch refs from its mirror:
    git -C "$shared" remote add linux-next "$next_preferred"
    git -C "$shared" fetch --tags --force --prune /mirror/linux-next.git '+refs/heads/*:refs/remotes/linux-next/*'
    git -C "$shared" worktree list
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import Git

DEFAULT_MIRRORS = [
    {"name": "kernel", "mirror": "/mirror/linux.git", "subpath": "shared/kernel/linux",
     "remotes": [
         {"name": "linux-next", "mirror": "/mirror/linux-next.git"},
         {"name": "linux-stable", "mirror": "/mirror/linux-stable.git"},
         {"name": "linux-modules", "mirror": "/mirror/linux-modules.git"},
     ]},
    {"name": "qemu", "mirror": "/mirror/qemu.git", "subpath": "shared/qemu/qemu"},
]


def main(mirrors: list[dict] | None = None, refresh: bool = True) -> dict:
    mirrors = mirrors or DEFAULT_MIRRORS

    git = Git()
    existing = git.capture("config", "--global", "--get-all", "safe.directory", check=False)
    if "*" not in existing.split("\n"):
        git.run("config", "--global", "--add", "safe.directory", "*")

    workers = Path(os.environ["WORKERS_DIR"])
    results = []
    for entry in mirrors:
        name, mirror, subpath = _validate(entry)
        remotes = entry.get("remotes") or []
        shared = workers / subpath
        upstreams = _upstreams(git, mirror, entry)
        preferred = _preferred(upstreams)
        origin = preferred or mirror
        action = _ensure(git, mirror, shared, preferred, remotes, refresh)
        remote_results = _ensure_remotes(git, shared, remotes, action == "cloned", refresh, name)
        head = git.capture("-C", str(shared), "rev-parse", "HEAD", check=False).strip() or None
        git.ok("-C", str(shared), "worktree", "list")
        print(f"{name}: {_progress(action, refresh)} (origin {origin})", flush=True)
        results.append({
            "name": name,
            "mirror": mirror,
            "upstreams": upstreams,
            "origin": origin,
            "shared": str(shared),
            "action": action,
            "head": head,
            "remotes": remote_results,
        })

    return {"workers_dir": str(workers), "mirrors": results}


def _validate(entry: dict) -> tuple[str, str, str]:
    """Validate one mirror entry and return its (name, mirror, subpath)."""
    name = entry.get("name")
    mirror = entry.get("mirror")
    subpath = entry.get("subpath")
    for key, value in (("name", name), ("mirror", mirror), ("subpath", subpath)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"mirror entry {entry!r}: {key} must be a non-empty string")
    _validate_upstreams(entry)
    if mirror.startswith("-"):
        raise ValueError(f"invalid mirror: {mirror}")
    if ".." in subpath or Path(subpath).is_absolute():
        raise ValueError(f"invalid subpath: {subpath}")
    return name, mirror, subpath


def _validate_remote(entry: dict) -> tuple[str, str]:
    """Validate one remote entry and return its (name, mirror)."""
    name = entry.get("name")
    mirror = entry.get("mirror")
    for key, value in (("name", name), ("mirror", mirror)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"remote entry {entry!r}: {key} must be a non-empty string")
    _validate_upstreams(entry)
    if mirror.startswith("-"):
        raise ValueError(f"invalid remote mirror: {mirror}")
    return name, mirror


def _validate_upstreams(entry: dict) -> None:
    """Require `upstreams`, if present, to be a list of non-empty strings."""
    upstreams = entry.get("upstreams")
    if upstreams is None:
        return
    if not isinstance(upstreams, list) or any(
            not isinstance(u, str) or not u for u in upstreams):
        raise ValueError(f"entry {entry!r}: upstreams must be a list of non-empty strings")


def _upstreams(git: Git, mirror: str, entry: dict) -> list[str]:
    """Resolve candidate upstream URLs: explicit list, explicit string, else derived."""
    explicit = entry.get("upstreams")
    if explicit:
        return list(explicit)
    one = entry.get("upstream")
    if one:
        return [one]
    origin = git.capture("-C", mirror, "config", "--get", "remote.origin.url",
                         check=False).strip()
    if not origin:
        return []
    https = origin
    if https.startswith("git://"):
        https = "https://" + https[len("git://"):]
    candidates = [https if https.startswith("https://") else None]
    if "git.kernel.org/" in https:
        path = https.split("git.kernel.org/", 1)[1]
        candidates.append(f"https://kernel.googlesource.com/{path}")
    candidates.append(origin)
    return list(dict.fromkeys(c for c in candidates if c))


def _preferred(upstreams: list[str]) -> str | None:
    """The preferred upstream URL: the first candidate, else None."""
    return upstreams[0] if upstreams else None


def _ensure(git: Git, mirror: str, shared: Path, preferred: str | None,
            remotes: list[dict], refresh: bool) -> str:
    """Ensure `shared` is a no-checkout clone of the local mirror; return the action taken."""
    fresh = not ((shared / ".git").exists() or (shared / "objects").is_dir())
    if fresh:
        shared.parent.mkdir(parents=True, exist_ok=True)
        refs = []
        for remote in remotes:
            refs += ["--reference-if-able", _validate_remote(remote)[1]]
        git.run("clone", "--no-checkout", "--shared", mirror, *refs, str(shared))
    _detach_head(git, shared)
    if preferred:
        git.ok("-C", str(shared), "remote", "set-url", "origin", preferred)
    if (fresh or refresh) and not git.ok("-C", str(shared), "fetch", "--tags", "--force",
                                         "--prune", mirror, "+refs/heads/*:refs/heads/*"):
        print(f"note: fetch of {shared} from {mirror} failed; using local refs", flush=True)
    return "cloned" if fresh else ("refreshed" if refresh else "present")


def _ensure_remotes(git: Git, shared: Path, remotes: list[dict], fresh: bool,
                    refresh: bool, label: str) -> list[dict]:
    """Wire each extra remote's alternate, remote URL and fetch; return per-remote actions."""
    if not remotes:
        return []
    _reconcile_alternates(shared, [_validate_remote(r)[1] for r in remotes])
    results = []
    for remote in remotes:
        rname, rmirror = _validate_remote(remote)
        rupstreams = _upstreams(git, rmirror, remote)
        url = _preferred(rupstreams) or rmirror
        if git.ok("-C", str(shared), "remote", "get-url", rname):
            git.ok("-C", str(shared), "remote", "set-url", rname, url)
            action = "present"
        else:
            git.ok("-C", str(shared), "remote", "add", rname, url)
            action = "added"
        if fresh or refresh:
            if git.ok("-C", str(shared), "fetch", "--tags", "--force", "--prune", rmirror,
                      f"+refs/heads/*:refs/remotes/{rname}/*"):
                action = "fetched"
            else:
                print(f"note: fetch of {shared} from {rmirror} ({rname}) failed; "
                      "using local refs", flush=True)
        print(f"{label}/{rname}: {action} (upstream {url})", flush=True)
        results.append({"name": rname, "upstreams": rupstreams, "action": action})
    return results


def _detach_head(git: Git, shared: Path) -> None:
    """Detach the shared clone's HEAD so a fetch into `refs/heads/*` is not refused."""
    branch = git.capture("-C", str(shared), "symbolic-ref", "--quiet", "HEAD", check=False).strip()
    if not branch:
        return
    sha = git.capture("-C", str(shared), "rev-parse", "HEAD", check=False).strip()
    if sha:
        git.run("-C", str(shared), "update-ref", "--no-deref", "HEAD", sha)


def _reconcile_alternates(shared: Path, mirrors: list[str]) -> None:
    """Append each mirror's `objects` dir to the clone's alternates file, deduped."""
    info = shared / ".git" / "objects" / "info"
    alternates = info / "alternates"
    present = alternates.read_text().splitlines() if alternates.exists() else []
    wanted = [str(Path(m) / "objects") for m in mirrors]
    missing = [p for p in wanted if p not in present]
    if not missing:
        return
    info.mkdir(parents=True, exist_ok=True)
    lines = present + missing
    alternates.write_text("\n".join(lines) + "\n")


def _progress(action: str, refresh: bool) -> str:
    """Render the per-mirror progress phrase for the action taken."""
    if action == "present":
        return "present (skip refresh)"
    return action
