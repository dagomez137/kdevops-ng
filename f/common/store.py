# SPDX-License-Identifier: copyleft-next-0.3.1
"""Nix-store transport for build identities (library, not a runnable step).

Imported with:  from f.common import store

Moves a built identity's run layer between hosts through the Nix store instead of
rsync. A builder publishes the tree with `nix store add-path` (its bytes land at a
content-addressed `/nix/store/...` path identical on every host) and registers an
identity->store-path index entry under `WORKERS_DIR/shared/store-index/<name>`; the
same entry is an indirect GC root, so the path survives `nix-collect-garbage`. A
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
    nix-store --add-root "$index/$name" --realise "$sp"

    # fetcher
    sp=$(ssh "$remote" readlink "$remote_index/$name")
    nix copy --from ssh://"$remote" "$sp" --no-check-sigs
    nix-store --add-root "$index/$name" --realise "$sp"
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import DevShell, Nix, run_logged

_INDEX_SUBDIR = "shared/store-index"


def main():
    """This module is a library imported by the build steps, not a runnable step."""
    return "f/common/store: Nix-store transport for build identities"


def index_dir() -> Path:
    """The local identity->store-path index (also the GC-root directory), created."""
    path = Path(os.environ["WORKERS_DIR"]) / _INDEX_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def publish(name: str, tree: str) -> str:
    """Add a tree to the store under `name`, index it + root it, return the store path."""
    sp = Nix().capture("store", "add-path", str(tree), "--name", name).strip()
    entry = index_dir() / name
    run_logged(["nix-store", "--add-root", str(entry), "--realise", sp])
    print(f"published {name} -> {sp}", flush=True)
    return sp


def link_local(name: str, sp: str) -> None:
    """Index + GC-root an already-valid store path, making this host a source for it."""
    entry = index_dir() / name
    run_logged(["nix-store", "--add-root", str(entry), "--realise", sp])
    print(f"indexed {name} -> {sp}", flush=True)


def local_path(name: str) -> str | None:
    """The store path indexed under `name` here, if the entry resolves to a real path.

    A pure read (no index-directory creation), so a probe step can call it before
    anything has been published and with `WORKERS_DIR` unset, getting `None` rather
    than a side effect or an error.
    """
    base = os.environ.get("WORKERS_DIR")
    if not base:
        return None
    entry = Path(base) / _INDEX_SUBDIR / name
    if entry.is_symlink():
        target = os.path.realpath(entry)
        if Path(target).exists():
            return target
    return None


def peer_path(workers: Path, remote: str, remote_index: str, name: str) -> str | None:
    """The store path the peer indexes under `name`, read over ssh, else None."""
    out = DevShell(workers, "transfer").capture(
        "ssh", remote, "readlink", f"{remote_index.rstrip('/')}/{name}", check=False).strip()
    return out or None


def fetch(workers: Path, remote: str, sp: str) -> None:
    """Copy a store path from the peer into the local store over ssh."""
    DevShell(workers, "transfer").run(
        "nix", "--extra-experimental-features", "nix-command",
        "copy", "--from", f"ssh://{remote}", sp, "--no-check-sigs")
