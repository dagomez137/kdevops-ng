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
provisioned by `f/workbench/mirror`; `build_mirrors()`/`remote_url()` here are the
shared source of truth for both. The entry's primary remote (the canonical tree, e.g.
`torvalds/linux`) supplies the Bare's `origin` URL.

`peers` is a list of `{host, store_index}` objects, one per other workbench host.
Each host becomes a `<peer>` remote on every Bare, its URL the peer's Bare under the
same SYSTEM_DIR layout (`ssh://<peer>/<SYSTEM_DIR>/bare/<project>.git`), with a
`+refs/heads/*:refs/remotes/<peer>/*` refspec. A developer publishes a branch
cross-host with `git -C <worktree> push <peer> <branch>`, and the peer's worker builds
it as a local `refs/heads/*` ref (ADR-0001's per-host ref channel). Not fetched here.
List peer hosts, not self.

Equivalent host bash (PATH includes /nix/var/nix/profiles/default/bin), per mirror:

    git config --global --add safe.directory '*'        # once per container
    mkdir --parents "$(dirname "$bare")"
    git init --bare "$bare"
    # borrow the ONE merged mirror's objects (every tree shares it) instead of
    # copying ($MIRRORS_DIR defaults to $SYSTEM_DIR/mirror); rewritten, not appended:
    printf '%s\n' "$MIRRORS_DIR"/linux.git/objects > "$bare/objects/info/alternates"
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

from f.common import store
from f.common.devshell import Git, mirrors_dir, system_dir

# Curated upstream hosts for the Linux mirror: source -> clone-URL template per
# transport. The `{path}` is the FULL path after the host (so fs/ trees sit beside
# kernel/git/ ones). kernel.org serves both https (the safe default) and the faster
# but often-firewalled git://; googlesource is the Google read-only mirror for when
# kernel.org itself is unreachable, https only.
LINUX_SOURCES = {
    "kernel.org": {
        "https": "https://git.kernel.org/{path}.git",
        "git": "git://git.kernel.org/{path}.git",
    },
    "googlesource": {
        "https": "https://kernel.googlesource.com/{path}.git",
    },
}
DEFAULT_LINUX_SOURCE = "kernel.org"

# The transport every source defaults to and is guaranteed to offer.
DEFAULT_PROTOCOL = "https"

# Curated Linux trees the form offers as a checklist: name -> path after git.kernel.org.
# The bare name is the label, the one a kernel developer or maintainer reads at a glance
# (torvalds, axboe, vfs, mcgrof); these are the core trees plus the set kdevops mirrors
# today, all one kernel object graph, so they live as remotes on one merged linux.git.
# Extend freely.
KERNEL_TREES = {
    "torvalds": "pub/scm/linux/kernel/git/torvalds/linux",
    "linux-next": "pub/scm/linux/kernel/git/next/linux-next",
    "linux-stable": "pub/scm/linux/kernel/git/stable/linux",
    "linux-stable-rc": "pub/scm/linux/kernel/git/stable/linux-stable-rc",
    "modules": "pub/scm/linux/kernel/git/modules/linux",
    "mcgrof": "pub/scm/linux/kernel/git/mcgrof/linux",
    "mcgrof-next": "pub/scm/linux/kernel/git/mcgrof/linux-next",
    "axboe": "pub/scm/linux/kernel/git/axboe/linux",
    "vfs": "pub/scm/linux/kernel/git/vfs/vfs",
    "cel": "pub/scm/linux/kernel/git/cel/linux",
    "jlayton": "pub/scm/linux/kernel/git/jlayton/linux",
    "cxl": "pub/scm/linux/kernel/git/cxl/cxl",
    "xfs": "pub/scm/fs/xfs/xfs-linux",
}

# Pre-checked when the operator does not pick: the common core.
DEFAULT_KERNEL_TREES = ["torvalds", "linux-next", "linux-stable", "modules", "axboe"]

# Curated upstream hosts for the QEMU mirror's origin: source -> clone URL per
# transport. GitLab is the canonical QEMU project repo (https or git://); GitHub is
# its read-only mirror, https only. The key is the stable lowercase value the step
# keys on; QEMU_SOURCE_LABELS carries the canonical display name for the form. Extend
# freely.
QEMU_SOURCES = {
    "gitlab": {
        "https": "https://gitlab.com/qemu-project/qemu.git",
        "git": "git://gitlab.com/qemu-project/qemu.git",
    },
    "github": {
        "https": "https://github.com/qemu/qemu.git",
    },
}
DEFAULT_QEMU_SOURCE = "gitlab"

# Canonical display labels for the qemu source dropdown, keyed by the stable value, so
# the form reads GitLab/GitHub while a saved selection survives the relabel.
QEMU_SOURCE_LABELS = {"gitlab": "GitLab", "github": "GitHub"}

# The curated projects a workbench can mirror, each its own merged bare mirror. The
# form offers these as a checklist; selecting one reveals its deploy options (linux:
# the trees plus a source and transport; qemu: a source and transport). Add a project
# here, a SOURCES table, and a builder in `build_mirrors` to extend the set.
MIRROR_PROJECTS = ["linux", "qemu"]
DEFAULT_MIRROR_PROJECTS = ["linux", "qemu"]


def remote_url(remote: dict) -> str:
    """The resolved clone URL of a mirror remote (composed by `build_mirrors`)."""
    return remote["url"]


def qemu_source_options(filter_text: str = "") -> list[dict]:
    """`[{label, value}]` for the qemu source dropdown: the canonical platform label
    (GitLab/GitHub) over the stable lowercase value, so a saved choice survives a label
    change and the step keys on a clean identifier."""
    options = [{"label": QEMU_SOURCE_LABELS[k], "value": k} for k in QEMU_SOURCES]
    return [o for o in options if filter_text.lower() in o["label"].lower()]


def list_qemu_sources(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_qemu_sources` entrypoint for the qemu `source` field."""
    return qemu_source_options(filterText)


def _effective_protocol(sources: dict, source: str, protocol: str) -> str:
    """The transport to use for `source`: the requested `protocol` when it offers it,
    else its preferred (first) one, with a note. A curated host may not serve every
    transport (the googlesource and GitHub mirrors are https only), so a `git` pick
    degrades to `https` rather than failing."""
    if source not in sources:
        raise ValueError(f"unknown source {source!r} (curated: {', '.join(sources)})")
    schemes = sources[source]
    if protocol in schemes:
        return protocol
    fallback = next(iter(schemes))
    print(
        f"note: source {source!r} has no {protocol!r} transport; using {fallback!r}",
        flush=True,
    )
    return fallback


def _linux_mirror(cfg: dict, mirror_dir: Path) -> dict:
    """The merged linux.git mirror from the linux `cfg` (`trees`, `source`, `protocol`):
    the curated `trees` (names in KERNEL_TREES) as remotes from one host over one
    transport. torvalds is always the primary object base (refs/heads/*); the rest land
    at refs/remotes/<name>/*."""
    trees = cfg.get("trees") or DEFAULT_KERNEL_TREES
    source = cfg.get("source") or DEFAULT_LINUX_SOURCE
    proto = _effective_protocol(
        LINUX_SOURCES, source, cfg.get("protocol") or DEFAULT_PROTOCOL
    )
    template = LINUX_SOURCES[source][proto]
    names = list(dict.fromkeys(["torvalds", *trees]))
    remotes = []
    for name in names:
        if name not in KERNEL_TREES:
            raise ValueError(
                f"unknown kernel tree {name!r} (curated: {', '.join(KERNEL_TREES)})"
            )
        remotes.append(
            {
                "name": name,
                "url": template.format(path=KERNEL_TREES[name]),
                "primary": name == "torvalds",
            }
        )
    return {
        "name": "linux",
        "project": "linux",
        "mirror": str(mirror_dir / "linux.git"),
        "remotes": remotes,
    }


def _qemu_mirror(cfg: dict, mirror_dir: Path) -> dict:
    """The qemu.git mirror from the qemu `cfg` (`source`, `protocol`): a single `origin`
    remote at the curated source over one transport."""
    source = cfg.get("source") or DEFAULT_QEMU_SOURCE
    proto = _effective_protocol(
        QEMU_SOURCES, source, cfg.get("protocol") or DEFAULT_PROTOCOL
    )
    return {
        "name": "qemu",
        "project": "qemu",
        "mirror": str(mirror_dir / "qemu.git"),
        "remotes": [
            {"name": "origin", "url": QEMU_SOURCES[source][proto], "primary": True}
        ],
    }


def build_mirrors(
    projects: list[str],
    linux: dict | None,
    qemu: dict | None,
    mirror_dir: Path,
) -> list[dict]:
    """Compose the mirror config for the selected `projects` (curated, in
    MIRROR_PROJECTS), each its own merged bare mirror under `mirror_dir`, from that
    project's deploy config (`linux`: trees + source + transport; `qemu`: source +
    transport). Both fetch and mirror drive off this one builder, so they can never
    disagree on the layout."""
    builders = {
        "linux": lambda: _linux_mirror(linux or {}, mirror_dir),
        "qemu": lambda: _qemu_mirror(qemu or {}, mirror_dir),
    }
    entries = []
    for project in projects:
        if project not in builders:
            raise ValueError(
                f"unknown mirror project {project!r} (curated: {', '.join(builders)})"
            )
        entries.append(builders[project]())
    return entries


def main(
    projects: list[str] | None = None,
    linux: dict | None = None,
    qemu: dict | None = None,
    peers: list[dict] | None = None,
    refresh: bool = True,
) -> dict:
    system = system_dir()
    mirrors = build_mirrors(
        DEFAULT_MIRROR_PROJECTS if projects is None else projects,
        linux,
        qemu,
        mirrors_dir(),
    )
    peers = _normalize_peers(peers)
    peer_hosts = [p["host"] for p in peers]

    git = Git()
    existing = git.capture(
        "config", "--global", "--get-all", "safe.directory", check=False
    )
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
        peer_results = _ensure_peers(git, bare, peer_hosts, system, project)
        head = (
            git.capture("-C", str(bare), "rev-parse", "HEAD", check=False).strip()
            or None
        )
        print(f"{name}: {_progress(action, refresh)} (origin {origin})", flush=True)
        results.append(
            {
                "name": name,
                "mirror": mirror,
                "origin": origin,
                "trees": trees,
                "bare": str(bare),
                "action": action,
                "head": head,
                "peers": peer_results,
            }
        )

    # Persist the peer registry where any worker can read it without touching git:
    # the qsu VM discovery (f.qsu.common.vm_options) sweeps these hosts over ssh.
    peers_file = system / "peers"
    peers_file.parent.mkdir(parents=True, exist_ok=True)
    peers_file.write_text("".join(f"{p['host']}\t{p['store_index']}\n" for p in peers))
    print(f"wrote {peers_file} ({len(peers)} peer(s))", flush=True)

    return {"system_dir": str(system), "mirrors": results, "peers": peers}


def _normalize_peers(peers: list[dict] | None) -> list[dict]:
    """Normalize the `peers` input to `[{host, store_index}]`, dropping empty entries.

    A bare string entry (legacy) is read as a host with the default-layout store-index;
    an object supplies an explicit `store_index`, falling back to that same default.
    """
    out = []
    for p in peers or []:
        if isinstance(p, str):
            host, index = p.strip(), store.DEFAULT_PEER_INDEX
        elif isinstance(p, dict):
            host = (p.get("host") or "").strip()
            index = (p.get("store_index") or "").strip() or store.DEFAULT_PEER_INDEX
        else:
            continue
        if host:
            out.append({"host": host, "store_index": index})
    return out


def _require_str(entry: dict, key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"mirror entry {entry!r}: {key} must be a non-empty string")
    return value


def _validate(entry: dict) -> tuple[str, str, str]:
    """Validate one mirror entry and return its (name, mirror, project)."""
    name = _require_str(entry, "name")
    mirror = _require_str(entry, "mirror")
    project = _require_str(entry, "project")
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


def _ensure(
    git: Git, mirror: str, bare: Path, origin: str, trees: list[str], refresh: bool
) -> str:
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
    git.run(
        "-C",
        str(bare),
        "config",
        "--replace-all",
        "remote.mirror.fetch",
        "+refs/heads/*:refs/remotes/mirror/*",
    )
    for tree in trees:
        git.run(
            "-C",
            str(bare),
            "config",
            "--add",
            "remote.mirror.fetch",
            f"+refs/remotes/{tree}/*:refs/remotes/{tree}/*",
        )
    if (fresh or refresh) and not git.ok(
        "-C", str(bare), "fetch", "--tags", "--force", "--prune", "mirror"
    ):
        print(
            f"note: fetch of {bare} from {mirror} failed; using local refs", flush=True
        )
    return "created" if fresh else ("refreshed" if refresh else "present")


def _ensure_peers(
    git: Git, bare: Path, peers: list[str], system: Path, project: str
) -> list[dict]:
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
        git.ok(
            "-C",
            str(bare),
            "config",
            f"remote.{peer}.fetch",
            f"+refs/heads/*:refs/remotes/{peer}/*",
        )
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
    """Point the bare repo's alternates at exactly the given mirrors' `objects` dirs.

    Authoritative, not append-only: any stale entry is dropped, so the bare ends
    with exactly these alternates.
    """
    info = bare / "objects" / "info"
    alternates = info / "alternates"
    present = alternates.read_text().splitlines() if alternates.exists() else []
    wanted = list(dict.fromkeys(str(Path(m) / "objects") for m in mirrors))
    if present == wanted:
        return
    info.mkdir(parents=True, exist_ok=True)
    alternates.write_text("".join(line + "\n" for line in wanted))
    print(f"wrote {alternates}: {' '.join(wanted)}", flush=True)


def _progress(action: str, refresh: bool) -> str:
    """Render the per-mirror progress phrase for the action taken."""
    if action == "present":
        return "present (skip refresh)"
    return action
