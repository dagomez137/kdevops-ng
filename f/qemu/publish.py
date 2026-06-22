# SPDX-License-Identifier: copyleft-next-0.3.1
"""Publish an installed QEMU identity's install tree to the Nix store.

Runnable step, the QEMU analog of `f/kernel/publish` and the publish half of the Store
transport (the `reuse_check`/`fetch_identity` family). Run only after a real install
(the flow skips it on reuse). The per-identity prefix `destdir/<identity>` IS exactly
one identity's tree, so the whole prefix is the run layer — it is added to the store
as-is, with no staging. A peer can then fetch it with `nix copy`. The store path is
identical on every host.

Returns the index `name`, the resolved `store_path`, and the `prefix`.

Equivalent bash, the prefix added to the store:

    nix store add-path "$prefix" --name qemu-"$(basename "$prefix")"
"""

from __future__ import annotations

from pathlib import Path

from f.common import store


def main(prefix: str) -> dict:
    identity = Path(prefix).name
    name = f"qemu-{identity}"
    sp = store.publish(name, prefix)
    return {"name": name, "store_path": sp, "prefix": prefix}
