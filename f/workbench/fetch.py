# SPDX-License-Identifier: copyleft-next-0.3.1
"""Provision the durable Bare main repos every build worktree is cut from.

Runnable step. For each entry it ensures the Bare at
`SYSTEM_DIR/bare/<project>.git` exists as a bare repo
(`git init --bare`) that borrows the ONE merged mirror's objects via its alternates
file (no objects copied, no upstream network). A single `mirror` remote points at the
local merged mirror with per-tree refspecs: the mirror's primary heads land in
`refs/remotes/mirror/*` (so worktree resolves `mirror/<ref>`), and every other tree the
mirror carries (`refs/remotes/<tree>/*`, e.g. `linux-next`, `axboe`) is copied through
unchanged, so a build can resolve `axboe/for-next` and friends. `origin` is set to the
primary remote's upstream URL purely so a human `git fetch origin` works. `refs/heads/*`
is left empty, reserved for developer pushes. Idempotent (ADR-0001: the Bare is the
working repo).

The mirror's own remotes (which upstream trees it carries and over which protocol) are
provisioned by `f/workbench/mirror`; `default_mirrors()`/`remote_url()` here are the
shared source of truth for both. The entry's primary remote (the canonical tree, e.g.
`torvalds/linux`) supplies the Bare's `origin` URL.

`peers` is a list of ssh-host aliases of other workbench hosts. Each becomes a
`<peer>` remote on every Bare, its URL the peer's Bare under the same SYSTEM_DIR
layout (`ssh://<peer>/<SYSTEM_DIR>/bare/<project>.git`), with a
`+refs/heads/*:refs/remotes/<peer>/*` refspec. A developer publishes a branch
cross-host with `git -C <worktree> push <peer> <branch>`, and the peer's worker builds
it as a local `refs/heads/*` ref (ADR-0001's per-host ref channel). Not fetched here.
List peer hosts, not self.

Equivalent host bash (PATH includes /nix/var/nix/profiles/default/bin), per mirror:

    git config --global --add safe.directory '*'        # once per container
    mkdir --parents "$(dirname "$bare")"
    git init --bare "$bare"
    # borrow the ONE merged mirror's objects (every tree shares it) instead of copying:
    printf '%s\n' "$SYSTEM_DIR"/mirror/linux.git/objects >> "$bare/objects/info/alternates"
    # origin = the primary tree's upstream, only for an explicit human `git fetch origin`:
    git -C "$bare" remote add origin git://git.kernel.org/.../torvalds/linux.git
    # one mirror remote, per-tree refspecs: primary heads -> refs/remotes/mirror/*,
    # every other tree (axboe, linux-next, ...) copied through unchanged:
    git -C "$bare" remote add mirror "$mirror"
    git -C "$bare" config --replace-all remote.mirror.fetch '+refs/heads/*:refs/remotes/mirror/*'
    git -C "$bare" config --add     remote.mirror.fetch '+refs/remotes/axboe/*:refs/remotes/axboe/*'
    git -C "$bare" fetch --tags --force --prune mirror
    # a peer host's Bare at the same SYSTEM_DIR layout, for cross-host dev branches:
    git -C "$bare" remote add hetzie "ssh://hetzie$SYSTEM_DIR/bare/linux.git"
    git -C "$bare" config remote.hetzie.fetch '+refs/heads/*:refs/remotes/hetzie/*'
"""

from __future__ import annotations

from pathlib import Path

from f.common.devshell import Git, system_dir

# A git tree at git.kernel.org by protocol. The `path` is the FULL path after the
# host (so fs/ trees sit beside kernel/git/ ones). git is fastest but often
# firewalled; https is the safe default; https-googlesource is the Google mirror for
# when kernel.org itself is unreachable.
_KERNEL_ORG = {
    "git": "git://git.kernel.org/{path}.git",
    "https": "https://git.kernel.org/{path}.git",
    "https-googlesource": "https://kernel.googlesource.com/{path}.git",
}

# Curated Linux trees the form offers as a checklist: name -> (path after git.kernel.org,
# human label). These are the core trees plus the set kdevops mirrors today; all are the
# kernel object graph, so they live as remotes on one merged linux.git. Extend freely.
KERNEL_TREES = {
    "torvalds":        ("pub/scm/linux/kernel/git/torvalds/linux",        "Mainline (Linus Torvalds)"),
    "linux-next":      ("pub/scm/linux/kernel/git/next/linux-next",        "linux-next integration"),
    "linux-stable":    ("pub/scm/linux/kernel/git/stable/linux",           "Stable"),
    "linux-stable-rc": ("pub/scm/linux/kernel/git/stable/linux-stable-rc", "Stable release candidates"),
    "modules":         ("pub/scm/linux/kernel/git/modules/linux",          "Modules (Luis Chamberlain)"),
    "mcgrof":          ("pub/scm/linux/kernel/git/mcgrof/linux",           "Luis Chamberlain"),
    "mcgrof-next":     ("pub/scm/linux/kernel/git/mcgrof/linux-next",      "Luis Chamberlain (next)"),
    "axboe":           ("pub/scm/linux/kernel/git/axboe/linux",            "Block, io_uring, NVMe (Jens Axboe)"),
    "vfs":             ("pub/scm/linux/kernel/git/vfs/vfs",                "VFS (Christian Brauner)"),
    "cel":             ("pub/scm/linux/kernel/git/cel/linux",              "NFS server (Chuck Lever)"),
    "jlayton":         ("pub/scm/linux/kernel/git/jlayton/linux",          "NFS / locks (Jeff Layton)"),
    "cxl":             ("pub/scm/linux/kernel/git/cxl/cxl",                "CXL"),
    "xfs":             ("pub/scm/fs/xfs/xfs-linux",                        "XFS"),
}

# Pre-checked when the operator does not pick: the common core.
DEFAULT_KERNEL_TREES = ["torvalds", "linux-next", "linux-stable", "modules", "axboe"]

DEFAULT_QEMU_URL = "https://gitlab.com/qemu-project/qemu.git"


def remote_url(remote: dict) -> str:
    """The clone URL for a mirror remote: an explicit `url`, or a `path` at git.kernel.org
    rendered with its `protocol` (git / https / https-googlesource)."""
    if remote.get("url"):
        return remote["url"]
    proto = remote.get("protocol", "https")
    if proto not in _KERNEL_ORG:
        raise ValueError(f"remote {remote.get('name')!r}: unknown protocol {proto!r} "
                         f"(want one of {', '.join(_KERNEL_ORG)})")
    if not remote.get("path"):
        raise ValueError(f"remote {remote.get('name')!r}: needs a git.kernel.org `path` "
                         "or an explicit `url`")
    return _KERNEL_ORG[proto].format(path=remote["path"])


def _extra_name(path: str) -> str:
    """A git remote name for a free-form kernel.org `path`: the maintainer when the leaf
    is the generic `linux`/`linux-next`, else the leaf (e.g. fs/xfs/xfs-linux -> xfs-linux)."""
    parts = path.strip("/").split("/")
    return parts[-2] if len(parts) >= 2 and parts[-1] in ("linux", "linux-next") else parts[-1]


def build_mirrors(kernel_trees: list[str], protocol: str, extra_trees: list[str],
                  mirror_dir: Path, qemu_url: str = DEFAULT_QEMU_URL) -> list[dict]:
    """Compose the full mirror config from a friendly selection: the curated kernel
    `kernel_trees` (names in KERNEL_TREES) plus free-form `extra_trees` (git.kernel.org
    paths), all over one `protocol`, as remotes on a single merged linux.git (torvalds is
    always the primary object base, at refs/heads/*; the rest at refs/remotes/<name>/*),
    plus the QEMU mirror. The advanced `mirrors` input bypasses this for full control."""
    names = list(dict.fromkeys(["torvalds", *kernel_trees]))
    remotes = []
    for name in names:
        if name not in KERNEL_TREES:
            raise ValueError(f"unknown kernel tree {name!r} (curated: {', '.join(KERNEL_TREES)})")
        path, _ = KERNEL_TREES[name]
        remotes.append({"name": name, "path": path, "protocol": protocol,
                        "primary": name == "torvalds"})
    for path in extra_trees:
        remotes.append({"name": _extra_name(path), "path": path, "protocol": protocol})
    return [
        {"name": "linux", "project": "linux",
         "mirror": str(mirror_dir / "linux.git"), "remotes": remotes},
        {"name": "qemu", "project": "qemu",
         "mirror": str(mirror_dir / "qemu.git"),
         "remotes": [{"name": "origin", "url": qemu_url, "primary": True}]},
    ]


def main(kernel_trees: list[str] | None = None, protocol: str = "https",
         extra_trees: list[str] | None = None, mirrors: list[dict] | None = None,
         peers: list[str] | None = None, refresh: bool = True) -> dict:
    system = system_dir()
    mirrors = mirrors or build_mirrors(
        DEFAULT_KERNEL_TREES if kernel_trees is None else kernel_trees,
        protocol, extra_trees or [], system / "mirror")
    peers = [p.strip() for p in (peers or []) if p and p.strip()]

    git = Git()
    existing = git.capture("config", "--global", "--get-all", "safe.directory", check=False)
    if "*" not in existing.split("\n"):
        git.run("config", "--global", "--add", "safe.directory", "*")

    results = []
    for entry in mirrors:
        name, mirror, project = _validate(entry)
        # Non-primary trees the merged mirror carries (refs/remotes/<tree>/*); the Bare
        # copies each through verbatim so a build can resolve e.g. `axboe/for-next`.
        trees = [r["name"] for r in entry["remotes"] if not r.get("primary")]
        bare = system / "bare" / f"{project}.git"
        origin = remote_url(_primary(entry))
        action = _ensure(git, mirror, bare, origin, trees, refresh)
        peer_results = _ensure_peers(git, bare, peers, system, project)
        head = git.capture("-C", str(bare), "rev-parse", "HEAD", check=False).strip() or None
        print(f"{name}: {_progress(action, refresh)} (origin {origin})", flush=True)
        results.append({
            "name": name,
            "mirror": mirror,
            "origin": origin,
            "trees": trees,
            "bare": str(bare),
            "action": action,
            "head": head,
            "peers": peer_results,
        })

    # Persist the peer registry where any worker can read it without touching git:
    # the qsu VM discovery (f.qsu.common.vm_options) sweeps these hosts over ssh.
    peers_file = system / "peers"
    peers_file.parent.mkdir(parents=True, exist_ok=True)
    peers_file.write_text("".join(f"{p}\n" for p in peers))
    print(f"wrote {peers_file} ({len(peers)} peer(s))", flush=True)

    return {"system_dir": str(system), "mirrors": results, "peers": peers}


def _validate(entry: dict) -> tuple[str, str, str]:
    """Validate one mirror entry and return its (name, mirror, project)."""
    name = entry.get("name")
    mirror = entry.get("mirror")
    project = entry.get("project")
    for key, value in (("name", name), ("mirror", mirror), ("project", project)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"mirror entry {entry!r}: {key} must be a non-empty string")
    if not entry.get("remotes"):
        raise ValueError(f"mirror {name!r}: needs at least one remote")
    if mirror.startswith("-"):
        raise ValueError(f"invalid mirror: {mirror}")
    if ".." in project or project.startswith("-") or Path(project).is_absolute():
        raise ValueError(f"invalid project: {project}")
    return name, mirror, project


def _primary(entry: dict) -> dict:
    """The entry's primary remote (the canonical tree at the mirror's refs/heads/*),
    else the first remote."""
    remotes = entry.get("remotes") or []
    for r in remotes:
        if r.get("primary"):
            return r
    return remotes[0]


def _ensure(git: Git, mirror: str, bare: Path, origin: str, trees: list[str],
            refresh: bool) -> str:
    """Ensure `bare` borrows the merged mirror's objects and has a single `mirror`
    remote that copies the mirror's primary heads to refs/remotes/mirror/* (so worktree
    resolves `mirror/<ref>`) and each extra tree (refs/remotes/<tree>/*) through
    unchanged. One alternate, one remote. Return the action taken."""
    fresh = not (bare / "objects").is_dir()
    if fresh:
        bare.parent.mkdir(parents=True, exist_ok=True)
        git.run("init", "--bare", str(bare))
    _reconcile_alternates(bare, [mirror])
    # origin: the primary upstream URL, only for an explicit human `git fetch origin`.
    if git.ok("-C", str(bare), "remote", "get-url", "origin"):
        git.ok("-C", str(bare), "remote", "set-url", "origin", origin)
    else:
        git.ok("-C", str(bare), "remote", "add", "origin", origin)
    # mirror: the local merged mirror. Per-tree refspecs (not a catch-all), so each
    # prunes only its own namespace and the mirror/* heads are never fought over.
    if git.ok("-C", str(bare), "remote", "get-url", "mirror"):
        git.ok("-C", str(bare), "remote", "set-url", "mirror", mirror)
    else:
        git.ok("-C", str(bare), "remote", "add", "mirror", mirror)
    git.run("-C", str(bare), "config", "--replace-all", "remote.mirror.fetch",
            "+refs/heads/*:refs/remotes/mirror/*")
    for tree in trees:
        git.run("-C", str(bare), "config", "--add", "remote.mirror.fetch",
                f"+refs/remotes/{tree}/*:refs/remotes/{tree}/*")
    if (fresh or refresh) and not git.ok("-C", str(bare), "fetch", "--tags", "--force",
                                         "--prune", "mirror"):
        print(f"note: fetch of {bare} from {mirror} failed; using local refs", flush=True)
    return "created" if fresh else ("refreshed" if refresh else "present")


def _ensure_peers(git: Git, bare: Path, peers: list[str], system: Path,
                  project: str) -> list[dict]:
    """Wire a `<peer>` remote per ssh-host alias -> that peer's Bare, deriving the URL
    from the shared SYSTEM_DIR layout. Adds the remote and its
    `+refs/heads/*:refs/remotes/<peer>/*` refspec; does not fetch (push is the workflow,
    the peer may be empty or unreachable). List peer hosts, not self.
    """
    results = []
    for peer in peers:
        _validate_peer(peer)
        url = f"ssh://{peer}{system}/bare/{project}.git"
        if git.ok("-C", str(bare), "remote", "get-url", peer):
            git.ok("-C", str(bare), "remote", "set-url", peer, url)
            action = "present"
        else:
            git.ok("-C", str(bare), "remote", "add", peer, url)
            action = "added"
        git.ok("-C", str(bare), "config", f"remote.{peer}.fetch",
               f"+refs/heads/*:refs/remotes/{peer}/*")
        print(f"{project}/{peer}: {action} ({url})", flush=True)
        results.append({"name": peer, "url": url})
    return results


def _validate_peer(peer: str) -> None:
    """A peer alias is both a git remote name and an ssh target, so reject path/flag chars."""
    if not isinstance(peer, str) or not peer:
        raise ValueError(f"peer must be a non-empty string: {peer!r}")
    if "/" in peer or ".." in peer or peer.startswith("-"):
        raise ValueError(f"invalid peer alias: {peer}")


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
