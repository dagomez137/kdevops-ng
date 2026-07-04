# SPDX-License-Identifier: copyleft-next-0.3.1
"""Publish the installed kselftests tree to the Nix store.

The selftests half of the publish family (`f/kernel/publish` for the run layer):
add the self-contained `kselftest_install` tree to the store under
`kselftests-<uts_release>`, indexed as a GC root, so the selftests suite flow (and
a peer, via `nix copy`) resolves it by kernel release. When the build step reused
an already-published tree (`install_dir` empty), resolve the local index entry
instead of re-publishing.

Equivalent bash:

    nix store add-path "$install_dir" --name kselftests-"$uts_release"
"""

from __future__ import annotations

from f.common import store


def main(install_dir: str, uts_release: str) -> dict:
    name = f"kselftests-{uts_release}"
    if not install_dir:
        sp = store.local_path(name)
        if not sp:
            raise RuntimeError(
                f"no install_dir and no published {name} in the store index; "
                "nothing to publish or reuse"
            )
        print(f"reuse: {name} -> {sp}", flush=True)
        return {
            "name": name,
            "store_path": sp,
            "uts_release": uts_release,
            "reused": True,
        }
    sp = store.publish(name, install_dir)
    return {"name": name, "store_path": sp, "uts_release": uts_release}
