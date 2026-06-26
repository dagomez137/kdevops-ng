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

Equivalent bash, run inside the nixos-flake transfer devShell, for each registered peer:

    sp=$(ssh "$host" readlink "$index"/qemu-"$(basename "$prefix")")
    nix copy --from ssh://"$host" "$sp" --no-check-sigs
    nix build "$sp" --out-link "$WORKERS_DIR/shared/store-index"/qemu-"$(basename "$prefix")"
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common import store


def main(prefix: str, use_peers: bool = True) -> dict:
    identity = Path(prefix).name
    if not use_peers:
        print(f"identity {identity}: peer fetch off, building locally", flush=True)
        return {"fetched": False, "prefix": prefix}

    workers = Path(os.environ["WORKERS_DIR"])
    name = f"qemu-{identity}"
    for peer in store.registered_peers():
        host = peer["host"]
        sp = store.peer_path(workers, host, peer["index"], name)
        if sp is None:
            continue
        store.fetch(workers, host, sp)
        store.link_local(name, sp)
        print(f"fetched install tree {identity} from {host} into the store", flush=True)
        return {"fetched": True, "prefix": prefix, "remote": host, "store_path": sp}

    print(f"identity {identity}: no registered peer has it", flush=True)
    return {"fetched": False, "prefix": prefix}
