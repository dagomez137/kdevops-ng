# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fetch a build identity's run layer from a peer builder through the Nix store.

The run-layer analog of `f/kernel/fetch_devel`, and the fetch half of the Store transport
(see `f/common/store` and `f/kernel/publish`). Run before the expensive compile: with
`use_peers` on, sweep the registered peers (`store.registered_peers()`, the
`$SYSTEM_DIR/peers` registry) and, for the first peer that published this build identity
(the baked kernelrelease), read its index entry over ssh to learn the store path, pull that
path with `nix copy`, and index it locally so this host becomes a source for it. The fetched
run layer, the boot image artifacts (`boot/<image>-<release>`, `System.map-<release>`,
`config-<release>`) and the `lib/modules/<release>/` tree, is left in the store; the
following `reuse_check` resolves the index entry and the build is skipped, consuming the run
layer from the store path with no copy.

`use_peers=False` (or no registered peer carrying the identity) does nothing; the build
proceeds locally.

Equivalent bash, run inside the nixos-flake transfer devShell, for each registered peer:

    sp=$(ssh "$host" readlink "$index"/kernel-"$uts_release")
    nix copy --from ssh://"$host" "$sp" --no-check-sigs
    nix build "$sp" --out-link "$STORE_INDEX_DIR"/kernel-"$uts_release"
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common import store


def main(destdir: str, uts_release: str, use_peers: bool = True) -> dict:
    if not use_peers:
        print(f"identity {uts_release}: peer fetch off, building locally", flush=True)
        return {"fetched": False, "uts_release": uts_release, "destdir": destdir}

    workers = Path(os.environ["WORKERS_DIR"])
    name = f"kernel-{uts_release}"
    for peer in store.registered_peers():
        host = peer["host"]
        sp = store.peer_path(workers, host, peer["index"], name)
        if sp is None:
            continue
        store.fetch(workers, host, sp)
        store.link_local(name, sp)
        print(f"fetched run layer {uts_release} from {host} into the store", flush=True)
        return {
            "fetched": True,
            "uts_release": uts_release,
            "destdir": destdir,
            "remote": host,
            "store_path": sp,
        }

    print(f"identity {uts_release}: no registered peer has it", flush=True)
    return {"fetched": False, "uts_release": uts_release, "destdir": destdir}
