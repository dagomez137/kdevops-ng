# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fetch a QEMU build identity's install tree from a peer builder through the Nix store.

The QEMU analog of `f/kernel/fetch_identity`, and the fetch half of the Store transport
(see `f/common/store` and `f/qemu/publish`). Run before the expensive compile: if a peer
host already published this build identity, read its index entry over ssh to learn the
store path, pull that path with `nix copy`, and materialize the whole install tree into
the local per-identity prefix, so the following `reuse_check` finds it present and the
build is skipped. The prefix IS the identity, so the entire tree is the run layer. The
fetched path is then indexed locally so this host becomes a source for it.

Same-host leaves `remote`/`remote_index` empty and does nothing — the prefix is already
where the build would install. Cross-host sets `remote` to an ssh host and `remote_index`
to that builder's `store-index` directory, read over ssh.

Equivalent bash, run inside the nixos-flake transfer devShell:

    sp=$(ssh "$remote" readlink "$remote_index"/qemu-"$(basename "$prefix")")
    nix copy --from ssh://"$remote" "$sp" --no-check-sigs
    cp --recursive --force "$sp"/. "$prefix"/
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common import store


def main(prefix: str, remote: str = "", remote_index: str = "") -> dict:
    if not (remote and remote_index):
        print(f"identity {prefix}: same-host, nothing to fetch", flush=True)
        return {"fetched": False, "prefix": prefix}

    workers = Path(os.environ["WORKERS_DIR"])
    identity = Path(prefix).name
    name = f"qemu-{identity}"
    sp = store.peer_path(workers, remote, remote_index, name)
    if sp is None:
        print(f"identity {identity}: peer {remote} has no such identity", flush=True)
        return {"fetched": False, "prefix": prefix}

    store.fetch(workers, remote, sp)
    store.materialize(sp, prefix)
    store.link_local(name, sp)
    print(f"fetched install tree from {remote} -> {prefix}", flush=True)

    return {"fetched": True, "prefix": prefix, "remote": remote, "store_path": sp}
