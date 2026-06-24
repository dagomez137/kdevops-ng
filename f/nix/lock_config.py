# SPDX-License-Identifier: copyleft-next-0.3.1
"""Lock a per-VM imageless config: materialise flake.lock for reproducibility.

Pins the per-VM flake's inputs (the vendored `nixos-flake` path input by narHash;
nixpkgs follows it) so the closure builds identically until explicitly refreshed.
The `path:` flakeref copies the whole config dir into the store, so (unlike a
bare/`git+file` flakeref) the files do NOT need to be git-tracked.

The `<pkg>-src` inputs are different: they are local source-override checkouts
(e.g. `xfstests-src`, `xfsprogs-src`) the operator iterates on, pinned to a branch.
They are re-locked to the branch tip on EVERY build so a freshly committed patch in
the checkout lands in the next closure without a manual lock bump; that is what
makes `f/qsu/bringup` (closure_source=build) leverage the checkout sources. The
vendored `nixos-flake` is only re-locked when `update` is set.

Equivalent bash:

    nix flake lock "path:$config_dir"
    # the local source overrides, always:
    nix flake update --flake "path:$config_dir" <pkg>-src ...
    # update=true, to re-pin the vendored nixos-flake after it changed:
    nix flake update --flake "path:$config_dir" nixos-flake
"""

from __future__ import annotations

import re
from pathlib import Path

from f.common.devshell import Nix


def _src_inputs(config_dir: str) -> list[str]:
    """The `<pkg>-src` flake inputs declared (uncommented) in the per-VM flake.nix.

    These are the local source-override checkouts; commented example inputs start
    with `#` and so never match the leading-whitespace anchor.
    """
    flake = Path(config_dir) / "flake.nix"
    if not flake.is_file():
        return []
    names = re.findall(r"(?m)^\s*([A-Za-z0-9_-]+-src)\s*=\s*\{", flake.read_text())
    return list(dict.fromkeys(names))


def main(config_dir: str, update: bool = False) -> dict:
    nix = Nix()
    ref = f"path:{config_dir}"
    nix.run("flake", "lock", ref)
    src = _src_inputs(config_dir)
    if src:
        print(
            f"+ re-locking source overrides to branch tip: {', '.join(src)}", flush=True
        )
        nix.run("flake", "update", "--flake", ref, *src)
    if update:
        nix.run("flake", "update", "--flake", ref, "nixos-flake")
    return {
        "config_dir": config_dir,
        "lock": str(Path(config_dir) / "flake.lock"),
        "src_inputs": src,
    }
