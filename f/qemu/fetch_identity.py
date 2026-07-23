# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fetch a QEMU build identity's install tree from a peer builder through the Nix store.

The QEMU analog of `f/kernel/fetch_identity`, and the fetch half of the Store transport
(see `f/common/store` and `f/qemu/publish`). Run before the expensive compile: with
`use_peers` on, sweep the registered peers (`store.registered_peers()`, the
`$SYSTEM_DIR/peers` registry) and, for the first that published this build identity, read
its index entry over ssh to learn the store path, pull that path with `nix copy`, then
index it locally so this host becomes a source for it. The fetched install tree is left in
the store; the following `reuse_check` resolves the index entry and the build is skipped,
consuming the emulator from the store path with no copy.

`use_peers=False` (or no registered peer carrying the identity) does nothing; the build
proceeds locally.

With `devel` on (the build flow sets it when a developer worktree is requested) the sweep
repeats for `qemu-devel-<identity>`, skipped when this host already has it. The two
layers are independent, so a peer's install tree says nothing about its devel layer;
fetching both here is what lets `reuse_check` report them both present and spares a full
rebuild whose only purpose would be to regenerate a layer the peer already published. It
stays off by default so a boot-oriented build never drags the devel layer.

Equivalent bash, run inside the nixos-flake transfer devShell, for each registered peer:

    sp=$(ssh "$host" readlink "$index"/qemu-"$(basename "$prefix")")
    nix copy --from ssh://"$host" "$sp" --no-check-sigs
    nix build "$sp" --out-link "$STORE_INDEX_DIR"/qemu-"$(basename "$prefix")"
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common import store


def main(prefix: str, use_peers: bool = True, devel: bool = False) -> dict:
    identity = Path(prefix).name
    if not use_peers:
        print(f"identity {identity}: peer fetch off, building locally", flush=True)
        return {"fetched": False, "prefix": prefix}

    workers = Path(os.environ["WORKERS_DIR"])
    out = {"fetched": False, "prefix": prefix}

    hit = store.fetch_from_peers(workers, f"qemu-{identity}")
    if hit is None:
        print(f"identity {identity}: no registered peer has it", flush=True)
    else:
        host, sp = hit
        print(f"fetched install tree {identity} from {host} into the store", flush=True)
        out.update({"fetched": True, "remote": host, "store_path": sp})

    if devel:
        out.update(_fetch_devel(workers, identity))
    return out


def _fetch_devel(workers: Path, identity: str) -> dict:
    """Sweep the peers for the devel layer too, unless this host already has it."""
    name = f"qemu-devel-{identity}"
    if store.local_path(name) is not None:
        print(f"devel layer {identity}: already in this store", flush=True)
        return {"devel_fetched": False}
    hit = store.fetch_from_peers(workers, name)
    if hit is None:
        print(f"devel layer {identity}: no registered peer has it", flush=True)
        return {"devel_fetched": False}
    host, sp = hit
    print(f"fetched devel layer {identity} from {host} into the store", flush=True)
    return {"devel_fetched": True, "devel_remote": host, "devel_store_path": sp}
