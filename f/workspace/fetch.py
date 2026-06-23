# SPDX-License-Identifier: copyleft-next-0.3.1
"""Provision the durable Bare main repos every build worktree is cut from.

Runnable step. For each entry it ensures the Bare at
`WORKERS_DIR/system/bare/<namespace>/<canonical>.git` exists as a bare repo
(`git init --bare`) that borrows the local mirror's objects via its alternates
file (no objects copied, no upstream network). A `mirror` remote points at the
local bare mirror and fetches its heads into `refs/remotes/mirror/*`; `origin`
is set to the preferred upstream URL purely so a human `git fetch origin` works.
`refs/heads/*` is left empty, reserved for developer pushes. Idempotent (ADR-0001:
the Bare is the working repo).

The upstream candidates come from the entry's `upstreams` list (user order), else its
back-compat `upstream` string, else are derived from the mirror's `remote.origin.url`
(https preferred, googlesource next, the raw `git://` last).

An entry may carry extra `remotes`, each `{name, mirror, upstream?/upstreams?}`: a git
remote whose URL is its own preferred upstream but whose refs are fetched from its own
local mirror into `refs/remotes/<name>/*`, and whose objects are borrowed via the
Bare's `objects/info/alternates` file alongside the main mirror's objects.

Equivalent host bash (PATH includes /nix/var/nix/profiles/default/bin), per mirror:

    git config --global --add safe.directory '*'        # once per container
    mkdir --parents "$(dirname "$bare")"
    git init --bare "$bare"
    # borrow the mirror objects (and each extra mirror's) instead of copying:
    printf '%s\n' /mirror/linux.git/objects >> "$bare/objects/info/alternates"
    printf '%s\n' /mirror/linux-next.git/objects >> "$bare/objects/info/alternates"
    # $preferred is the first of the per-entry upstream list (https preferred; git://
    # is often blocked); used only for an explicit human `git fetch origin`:
    git -C "$bare" remote add origin "$preferred"
    # the mirror remote: its heads land in refs/remotes/mirror/*, never refs/heads/*:
    git -C "$bare" remote add mirror "$mirror"
    git -C "$bare" config remote.mirror.fetch '+refs/heads/*:refs/remotes/mirror/*'
    git -C "$bare" fetch --tags --force --prune mirror
    # add each extra remote (URL = its preferred upstream) and fetch refs from its mirror:
    git -C "$bare" remote add linux-next "$next_preferred"
    git -C "$bare" fetch --tags --force --prune /mirror/linux-next.git '+refs/heads/*:refs/remotes/linux-next/*'
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import Git

DEFAULT_MIRRORS = [
    {"name": "kernel", "mirror": "/mirror/linux.git",
     "namespace": "kernel", "canonical": "linux",
     "remotes": [
         {"name": "linux-next", "mirror": "/mirror/linux-next.git"},
         {"name": "linux-stable", "mirror": "/mirror/linux-stable.git"},
         {"name": "linux-modules", "mirror": "/mirror/linux-modules.git"},
     ]},
    {"name": "qemu", "mirror": "/mirror/qemu.git",
     "namespace": "qemu-project", "canonical": "qemu"},
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
        name, mirror, namespace, canonical = _validate(entry)
        remotes = entry.get("remotes") or []
        bare = workers / "system" / "bare" / namespace / f"{canonical}.git"
        upstreams = _upstreams(git, mirror, entry)
        preferred = _preferred(upstreams)
        origin = preferred or mirror
        action = _ensure(git, mirror, bare, preferred, remotes, refresh)
        remote_results = _ensure_remotes(git, bare, remotes, action == "created", refresh, name)
        head = git.capture("-C", str(bare), "rev-parse", "HEAD", check=False).strip() or None
        print(f"{name}: {_progress(action, refresh)} (origin {origin})", flush=True)
        results.append({
            "name": name,
            "mirror": mirror,
            "upstreams": upstreams,
            "origin": origin,
            "bare": str(bare),
            "action": action,
            "head": head,
            "remotes": remote_results,
        })

    return {"workers_dir": str(workers), "mirrors": results}


def _validate(entry: dict) -> tuple[str, str, str, str]:
    """Validate one mirror entry and return its (name, mirror, namespace, canonical)."""
    name = entry.get("name")
    mirror = entry.get("mirror")
    namespace = entry.get("namespace")
    canonical = entry.get("canonical")
    for key, value in (("name", name), ("mirror", mirror),
                       ("namespace", namespace), ("canonical", canonical)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"mirror entry {entry!r}: {key} must be a non-empty string")
    _validate_upstreams(entry)
    if mirror.startswith("-"):
        raise ValueError(f"invalid mirror: {mirror}")
    for key, value in (("namespace", namespace), ("canonical", canonical)):
        if ".." in value or value.startswith("-") or Path(value).is_absolute():
            raise ValueError(f"invalid {key}: {value}")
    return name, mirror, namespace, canonical


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


def _ensure(git: Git, mirror: str, bare: Path, preferred: str | None,
            remotes: list[dict], refresh: bool) -> str:
    """Ensure `bare` is a bare repo borrowing the mirror objects; return the action taken."""
    fresh = not (bare / "objects").is_dir()
    if fresh:
        bare.parent.mkdir(parents=True, exist_ok=True)
        git.run("init", "--bare", str(bare))
    _reconcile_alternates(bare, [mirror] + [_validate_remote(r)[1] for r in remotes])
    if preferred:
        if git.ok("-C", str(bare), "remote", "get-url", "origin"):
            git.ok("-C", str(bare), "remote", "set-url", "origin", preferred)
        else:
            git.ok("-C", str(bare), "remote", "add", "origin", preferred)
    if git.ok("-C", str(bare), "remote", "get-url", "mirror"):
        git.ok("-C", str(bare), "remote", "set-url", "mirror", mirror)
    else:
        git.ok("-C", str(bare), "remote", "add", "mirror", mirror)
    git.ok("-C", str(bare), "config", "remote.mirror.fetch",
           "+refs/heads/*:refs/remotes/mirror/*")
    if (fresh or refresh) and not git.ok("-C", str(bare), "fetch", "--tags", "--force",
                                         "--prune", "mirror"):
        print(f"note: fetch of {bare} from {mirror} failed; using local refs", flush=True)
    return "created" if fresh else ("refreshed" if refresh else "present")


def _ensure_remotes(git: Git, bare: Path, remotes: list[dict], fresh: bool,
                    refresh: bool, label: str) -> list[dict]:
    """Wire each extra remote's remote URL and fetch; return per-remote actions."""
    results = []
    for remote in remotes:
        rname, rmirror = _validate_remote(remote)
        rupstreams = _upstreams(git, rmirror, remote)
        url = _preferred(rupstreams) or rmirror
        if git.ok("-C", str(bare), "remote", "get-url", rname):
            git.ok("-C", str(bare), "remote", "set-url", rname, url)
            action = "present"
        else:
            git.ok("-C", str(bare), "remote", "add", rname, url)
            action = "added"
        if fresh or refresh:
            if git.ok("-C", str(bare), "fetch", "--tags", "--force", "--prune", rmirror,
                      f"+refs/heads/*:refs/remotes/{rname}/*"):
                action = "fetched"
            else:
                print(f"note: fetch of {bare} from {rmirror} ({rname}) failed; "
                      "using local refs", flush=True)
        print(f"{label}/{rname}: {action} (upstream {url})", flush=True)
        results.append({"name": rname, "upstreams": rupstreams, "action": action})
    return results


def _reconcile_alternates(bare: Path, mirrors: list[str]) -> None:
    """Append each mirror's `objects` dir to the bare repo's alternates file, deduped."""
    info = bare / "objects" / "info"
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
