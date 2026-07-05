# SPDX-License-Identifier: copyleft-next-0.3.1
"""Lay the built usertests harness tree onto the guest's `usertests` share.

Copies the published Nix store tree `f/usertests/discover` resolved to
`<share>/<kver>/tree/`, the directory the guest's
`usertests@<instance>.service` executes from
(`ExecStart=... /var/lib/usertests/%v/tree/%I`). When the copied tree's
`MANIFEST` already matches the store's, the copy is skipped; otherwise any
stale tree is removed and replaced, and the copy is opened for writing
(`chmod --recursive u+w`: store modes are read-only). The host never contacts
the guest.

Equivalent commands:

    rm --recursive --force "$WORKERS_DIR/shared/usertests/<vm>/<kver>/tree"
    mkdir --parents        "$WORKERS_DIR/shared/usertests/<vm>/<kver>/tree"
    cp --archive <store>/. "$WORKERS_DIR/shared/usertests/<vm>/<kver>/tree"
    chmod --recursive u+w  "$WORKERS_DIR/shared/usertests/<vm>/<kver>/tree"
"""

from __future__ import annotations

import shutil
from pathlib import Path

from f.common.devshell import run_logged
from f.common.remote import list_vms as _list_vms
from f.usertests.common import share_dir


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def _same_tree(src: Path, dest: Path) -> bool:
    """Whether `dest` already carries `src`'s tree: its `MANIFEST` present and
    byte-identical to the store's."""
    s, d = src / "MANIFEST", dest / "MANIFEST"
    return d.is_file() and s.is_file() and s.read_text() == d.read_text()


def main(vm_name: str, kernel_version: str, store_path: str) -> dict:
    src = Path(store_path)
    dest = share_dir(vm_name) / kernel_version / "tree"

    if _same_tree(src, dest):
        print(f"+ reusing {dest} (MANIFEST matches {src})", flush=True)
        return {"vm": vm_name, "tree": str(dest), "reused": True}

    if dest.exists():
        shutil.rmtree(dest)
        print(f"+ removed stale {dest}", flush=True)
    dest.mkdir(parents=True)
    run_logged(["cp", "--archive", f"{src}/.", str(dest)])
    # Store modes are read-only; open the copy for later replacement.
    run_logged(["chmod", "--recursive", "u+w", str(dest)])
    print(f"+ copied {src} -> {dest}", flush=True)
    return {"vm": vm_name, "tree": str(dest), "reused": False}
