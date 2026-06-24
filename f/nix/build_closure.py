# SPDX-License-Identifier: copyleft-next-0.3.1
"""Build the imageless NixOS toplevel closure and read its bootspec.

`nix build path:<dir>#toplevel` realises `nixosConfigurations.vm.config.system.build
.toplevel`: the system closure a VM boots over virtiofs. The closure carries a
standard NixOS bootspec (RFC-0125) at `<toplevel>/boot.json`; `init` and `initrd`
are read from `org.nixos.bootspec.v1`. The imageless closure has no `$out/initrd`
symlink, so the bootspec is the source of truth for those paths.

Equivalent bash:

    nix build "path:$config_dir#toplevel" --out-link "$config_dir/result" --print-out-paths
    jq -r '."org.nixos.bootspec.v1" | .init, .initrd' "$config_dir/result/boot.json"
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from f.common.devshell import Nix


def main(config_dir: str) -> dict:
    cfg = Path(config_dir)
    result = cfg / "result"

    nix = Nix()
    toplevel = nix.capture(
        "build", f"path:{config_dir}#toplevel", "--out-link", str(result), "--print-out-paths"
    ).strip() or os.path.realpath(result)

    spec = json.loads((result / "boot.json").read_text())["org.nixos.bootspec.v1"]
    init, initrd = spec["init"], spec["initrd"]
    print(f"toplevel={toplevel}\ninit={init}\ninitrd={initrd}", flush=True)

    return {
        "config_dir": config_dir,
        "toplevel": toplevel,
        "init": init,
        "initrd": initrd,
        "boot_json": str(result / "boot.json"),
    }
