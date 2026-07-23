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

With `devel` on (the build flow sets it when a developer worktree is requested) the
sweep repeats for `kernel-devel-<release>`, skipped when this host already has it. The
two layers are independent, so a peer's run layer says nothing about its devel layer;
fetching both here is what lets `reuse_check` report them both present and spares a full
rebuild whose only purpose would be to regenerate a layer the peer already published. It
stays off by default so a boot-oriented build never drags the much larger devel layer.

Equivalent bash, run inside the nixos-flake transfer devShell, for each registered peer:

    sp=$(ssh "$host" readlink "$index"/kernel-"$uts_release")
    nix copy --from ssh://"$host" "$sp" --no-check-sigs
    nix build "$sp" --out-link "$STORE_INDEX_DIR"/kernel-"$uts_release"
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common import store


def main(
    destdir: str, uts_release: str, use_peers: bool = True, devel: bool = False
) -> dict:
    if not use_peers:
        print(f"identity {uts_release}: peer fetch off, building locally", flush=True)
        return {"fetched": False, "uts_release": uts_release, "destdir": destdir}

    workers = Path(os.environ["WORKERS_DIR"])
    out = {"fetched": False, "uts_release": uts_release, "destdir": destdir}

    hit = store.fetch_from_peers(workers, f"kernel-{uts_release}")
    if hit is None:
        print(f"identity {uts_release}: no registered peer has it", flush=True)
    else:
        host, sp = hit
        print(f"fetched run layer {uts_release} from {host} into the store", flush=True)
        out.update({"fetched": True, "remote": host, "store_path": sp})

    if devel:
        out.update(_fetch_devel(workers, uts_release))
    return out


def _fetch_devel(workers: Path, uts_release: str) -> dict:
    """Sweep the peers for the devel layer too, unless this host already has it."""
    name = f"kernel-devel-{uts_release}"
    if store.local_path(name) is not None:
        print(f"devel layer {uts_release}: already in this store", flush=True)
        return {"devel_fetched": False}
    hit = store.fetch_from_peers(workers, name)
    if hit is None:
        print(f"devel layer {uts_release}: no registered peer has it", flush=True)
        return {"devel_fetched": False}
    host, sp = hit
    print(f"fetched devel layer {uts_release} from {host} into the store", flush=True)
    return {"devel_fetched": True, "devel_remote": host, "devel_store_path": sp}
