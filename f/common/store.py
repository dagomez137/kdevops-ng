# SPDX-License-Identifier: copyleft-next-0.3.1
"""Nix-store transport for build identities (library, not a runnable step).

Imported with:  from f.common import store

Moves a built identity's run layer between hosts through the Nix store instead of
rsync. A builder publishes the tree with `nix store add-path` (its bytes land at a
content-addressed `/nix/store/...` path identical on every host) and registers an
identity->store-path index entry under `SYSTEM_DIR/store-index/<name>`; the
same entry is an indirect GC root, so the path survives `nix store gc`. A
fetcher reads the peer's index entry over ssh to learn the store path, pulls it with
`nix copy --from ssh://<remote>`, and registers the path under its own index so it
becomes a source for the next host. The fetched run layer is consumed in place from
the store (the `reuse_check` step resolves the local index entry), so nothing is
copied out of it.

Store paths are absolute and identical on every host, so the path read from a peer
is the path to copy, with no rewriting.

Equivalent bash, run inside the nixos-flake transfer devShell for the cross-host half:

    # publisher
    sp=$(nix store add-path "$tree" --name "$name")
    nix build "$sp" --out-link "$index/$name"

    # fetcher
    sp=$(ssh "$remote" readlink "$remote_index/$name")
    nix copy --from ssh://"$remote" "$sp" --no-check-sigs
    nix build "$sp" --out-link "$index/$name"
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import DevShell, Nix, store_index_dir, system_dir

# Default-layout path to a peer's store-index, used when a peers-registry line names
# only a host. ssh runs `readlink` in the peer's shell, which expands the leading `~`.
DEFAULT_PEER_INDEX = "~/.local/state/windmill/workbench/system/store-index"


def main():
    """This module is a library imported by the build steps, not a runnable step."""
    return "f/common/store: Nix-store transport for build identities"


def index_dir() -> Path:
    """The local identity->store-path index (also the GC-root directory), created."""
    path = store_index_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def registered_peers() -> list[dict]:
    """Registered peers from `$SYSTEM_DIR/peers`: one `<host> [<store_index_dir>]` per line.

    The single source of truth for the peer registry, written by f/workbench/fetch and
    read by every consumer (peer auto-discovery in `fetch_identity`, qsu VM discovery).
    The first token is the ssh host; an optional second token is that peer's store-index
    dir (its `SYSTEM_DIR/store-index`), defaulting to the default-layout path when a
    legacy host-only line omits it. A missing or empty file means no peers.
    """
    f = system_dir() / "peers"
    if not f.is_file():
        return []
    peers = []
    for line in f.read_text().splitlines():
        toks = line.split()
        if not toks:
            continue
        index = toks[1] if len(toks) > 1 else DEFAULT_PEER_INDEX
        peers.append({"host": toks[0], "index": index})
    return peers


def publish(name: str, tree: str) -> str:
    """Add a tree to the store under `name`, index it + root it, return the store path."""
    sp = Nix().capture("store", "add-path", str(tree), "--name", name).strip()
    entry = index_dir() / name
    Nix().run("build", sp, "--out-link", str(entry))
    print(f"published {name} -> {sp}", flush=True)
    return sp


def link_local(name: str, sp: str) -> None:
    """Index + GC-root an already-valid store path, making this host a source for it."""
    entry = index_dir() / name
    Nix().run("build", sp, "--out-link", str(entry))
    print(f"indexed {name} -> {sp}", flush=True)


def local_path(name: str) -> str | None:
    """The store path indexed under `name` here, if the entry resolves to a real path.

    A pure read (no index-directory creation), so a probe step can call it before
    anything has been published. It resolves the System-workbench index and returns
    `None` rather than a side effect or an error when the env cannot resolve it.
    """
    try:
        entry = store_index_dir() / name
    except KeyError:
        return None
    if entry.is_symlink():
        target = os.path.realpath(entry)
        if Path(target).exists():
            return target
    return None


def list_index(prefix: str) -> list[str]:
    """Index entry names under `prefix` that resolve to a live store path (pure read).

    Backs the bringup dynselect pickers (`kernel-`, `qemu-`). Like `local_path` it
    creates nothing and returns `[]` when the env cannot resolve the index; a dangling
    entry (its store path GC'd) is skipped so it never reaches a dropdown. Local index
    only: a peer's artifact is already registered here by `fetch_identity` ->
    `link_local`, and the dynselect runtime has no host bus or peer ssh.
    """
    try:
        d = store_index_dir()
    except KeyError:
        return []
    if not d.is_dir():
        return []
    names = []
    for entry in sorted(d.iterdir()):
        if entry.name.startswith(prefix) and entry.is_symlink():
            if Path(os.path.realpath(entry)).exists():
                names.append(entry.name)
    return names


def latest_index(prefix: str) -> str | None:
    """The most recently indexed live entry under `prefix`, by GC-root mtime, else None.

    Lets a reuse with no explicit pick fall back to the freshly built or fetched
    artifact. A pure read like `list_index`; skips dangling entries.
    """
    try:
        d = store_index_dir()
    except KeyError:
        return None
    if not d.is_dir():
        return None
    best, best_mtime = None, -1.0
    for entry in d.iterdir():
        if not (entry.name.startswith(prefix) and entry.is_symlink()):
            continue
        if not Path(os.path.realpath(entry)).exists():
            continue
        mtime = entry.lstat().st_mtime
        if mtime > best_mtime:
            best, best_mtime = entry.name, mtime
    return best


def peer_path(workers: Path, remote: str, remote_index: str, name: str) -> str | None:
    """The store path the peer indexes under `name`, read over ssh, else None."""
    out = (
        DevShell(workers, "transfer")
        .capture(
            "ssh", remote, "readlink", f"{remote_index.rstrip('/')}/{name}", check=False
        )
        .strip()
    )
    return out or None


def fetch(workers: Path, remote: str, sp: str) -> None:
    """Copy a store path from the peer into the local store over ssh."""
    DevShell(workers, "transfer").run(
        "nix",
        "--extra-experimental-features",
        "nix-command",
        "copy",
        "--from",
        f"ssh://{remote}",
        sp,
        "--no-check-sigs",
    )
