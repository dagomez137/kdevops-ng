# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fetch a build identity's run layer from a peer builder through the Nix store.

The run-layer analog of `f/kernel/fetch_devel`, and the fetch half of the Store transport
(see `f/common/store` and `f/kernel/publish`). Run before the expensive compile: if a peer
host already published this build identity (the baked kernelrelease), read its index entry
over ssh to learn the store path and pull that path with `nix copy`, then index it locally
so this host becomes a source for it. The fetched run layer — the boot image artifacts
(`boot/<image>-<release>`, `System.map-<release>`, `config-<release>`) and the
`lib/modules/<release>/` tree — is left in the store; the following `reuse_check` resolves
the index entry and the build is skipped, consuming the run layer from the store path with
no copy.

Same-host leaves `remote`/`remote_index` empty and does nothing — a local build installs
into the destdir directly. Cross-host sets `remote` to an ssh host and `remote_index` to
that builder's `store-index` directory, read over ssh.

Equivalent bash, run inside the nixos-flake transfer devShell:

    sp=$(ssh "$remote" readlink "$remote_index"/kernel-"$uts_release")
    nix copy --from ssh://"$remote" "$sp" --no-check-sigs
    nix build "$sp" --out-link "$index"/kernel-"$uts_release"
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common import store


def main(
    destdir: str,
    uts_release: str,
    remote: str = "",
    remote_index: str = "",
) -> dict:
    if not (remote and remote_index):
        print(f"identity {uts_release}: same-host, nothing to fetch", flush=True)
        return {"fetched": False, "uts_release": uts_release, "destdir": destdir}

    workers = Path(os.environ["WORKERS_DIR"])
    name = f"kernel-{uts_release}"
    sp = store.peer_path(workers, remote, remote_index, name)
    if sp is None:
        print(f"identity {uts_release}: peer {remote} has no such identity", flush=True)
        return {"fetched": False, "uts_release": uts_release, "destdir": destdir}

    store.fetch(workers, remote, sp)
    store.link_local(name, sp)
    print(f"fetched run layer {uts_release} from {remote} into the store", flush=True)

    return {
        "fetched": True,
        "uts_release": uts_release,
        "destdir": destdir,
        "remote": remote,
        "store_path": sp,
    }
