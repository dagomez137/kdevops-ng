# SPDX-License-Identifier: copyleft-next-0.3.1
"""Publish an installed kernel identity's run layer to the Nix store.

Runnable step, the publish half of the Store transport (the `reuse_check`/`fetch_identity`
family). Run only after a real install (the flow skips it on reuse): stage just this
release's run layer — the boot image artifacts (`boot/<image>-<release>`,
`System.map-<release>`, `config-<release>`) and the `lib/modules/<release>/` tree, not
the whole multi-release destdir — and add it to the store. A peer can then fetch it by
release with `nix copy`. The store path is identical on every host.

Returns the index `name`, the resolved `store_path`, and the `uts_release`.

Equivalent bash, the staged tree then added to the store:

    cp --recursive --force "$destdir"/boot/*-"$uts_release" "$stage"/boot/
    cp --recursive --force "$destdir"/lib/modules/"$uts_release" \\
        "$stage"/lib/modules/"$uts_release"
    nix store add-path "$stage" --name kernel-"$uts_release"
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from f.common import store
from f.common.devshell import run_logged


def main(destdir: str, uts_release: str) -> dict:
    name = f"kernel-{uts_release}"
    src = Path(destdir)
    images = sorted(src.glob(f"boot/*-{uts_release}"))

    stage = Path(tempfile.mkdtemp(prefix=f"{name}-"))
    try:
        (stage / "boot").mkdir(parents=True, exist_ok=True)
        (stage / "lib/modules").mkdir(parents=True, exist_ok=True)
        for image in images:
            run_logged(["cp", "--recursive", "--force",
                        str(image), str(stage / "boot" / image.name)])
        run_logged(["cp", "--recursive", "--force",
                    str(src / "lib/modules" / uts_release),
                    str(stage / "lib/modules" / uts_release)])
        sp = store.publish(name, str(stage))
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    return {"name": name, "store_path": sp, "uts_release": uts_release}
