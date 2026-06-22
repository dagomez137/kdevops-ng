# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fetch a QEMU build identity's install tree from a peer builder into the prefix.

The QEMU analog of `f/kernel/fetch_identity`. Run before the expensive compile: if a
peer host already installed this build identity, pull its whole install tree into the
local per-identity prefix, so the following `reuse_check` finds it present and the
build is skipped. The prefix IS the identity, so the entire `<prefix>/` tree is the
run layer — the whole tree is mirrored, with no per-release include filter.

Same-host leaves `remote`/`remote_prefix` empty and does nothing — the prefix is
already where the build would install. Cross-host sets `remote` to an ssh host and
`remote_prefix` to that builder's per-identity prefix, read over ssh.

Equivalent bash, run inside the nixos-flake transfer devShell:

    mkdir --parents "$prefix"
    rsync --archive --no-owner --no-group \\
        "$remote":"$remote_prefix"/ "$prefix"/
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import DevShell


def main(prefix: str, remote: str = "", remote_prefix: str = "") -> dict:
    if not (remote and remote_prefix):
        print(f"identity {prefix}: same-host, nothing to fetch", flush=True)
        return {"fetched": False, "prefix": prefix}

    dest = Path(prefix)
    dest.mkdir(parents=True, exist_ok=True)

    src_root = remote_prefix.rstrip("/")
    shell = DevShell(Path(os.environ["WORKERS_DIR"]), "transfer")
    shell.run("rsync", "--archive", "--no-owner", "--no-group",
              f"{remote}:{src_root}/", str(dest) + "/")
    print(f"fetched install tree from {remote} -> {prefix}", flush=True)

    return {"fetched": True, "prefix": prefix, "remote": remote}
