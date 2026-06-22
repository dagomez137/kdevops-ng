# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fetch a build identity's run layer from a peer builder into the local destdir.

The run-layer analog of `f/kernel/fetch_devel`. Run before the expensive compile: if a
peer host already installed this build identity (the baked kernelrelease), pull its run
layer — the boot image artifacts (`boot/<image>-<release>`, `System.map-<release>`,
`config-<release>`) and the `lib/modules/<release>/` tree — into the local destdir, so
the following `reuse_check` finds them present and the build is skipped.

Same-host leaves `remote`/`remote_destdir` empty and does nothing — the destdir is
already where the build would install. Cross-host sets `remote` to an ssh host and
`remote_destdir` to that builder's destdir, read over ssh.

Equivalent bash, run inside the nixos-flake transfer devShell:

    mkdir --parents "$destdir/boot" "$destdir/lib/modules/$uts_release"
    rsync --archive --no-owner --no-group \
        --include='*-'"$uts_release" --exclude='*' \
        "$remote":"$remote_destdir"/boot/ "$destdir"/boot/
    rsync --archive --no-owner --no-group \
        "$remote":"$remote_destdir"/lib/modules/"$uts_release"/ \
        "$destdir"/lib/modules/"$uts_release"/
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import DevShell


def main(
    destdir: str,
    uts_release: str,
    remote: str = "",
    remote_destdir: str = "",
) -> dict:
    if not (remote and remote_destdir):
        print(f"identity {uts_release}: same-host, nothing to fetch", flush=True)
        return {"fetched": False, "uts_release": uts_release, "destdir": destdir}

    dest = Path(destdir)
    boot = dest / "boot"
    modules = dest / "lib/modules" / uts_release
    boot.mkdir(parents=True, exist_ok=True)
    modules.mkdir(parents=True, exist_ok=True)

    src_root = remote_destdir.rstrip("/")
    shell = DevShell(Path(os.environ["WORKERS_DIR"]), "transfer")
    shell.run("rsync", "--archive", "--no-owner", "--no-group",
              f"--include=*-{uts_release}", "--exclude=*",
              f"{remote}:{src_root}/boot/", str(boot) + "/")
    shell.run("rsync", "--archive", "--no-owner", "--no-group",
              f"{remote}:{src_root}/lib/modules/{uts_release}/",
              str(modules) + "/")
    print(f"fetched run layer {uts_release} from {remote}", flush=True)

    return {
        "fetched": True,
        "uts_release": uts_release,
        "destdir": destdir,
        "remote": remote,
    }
