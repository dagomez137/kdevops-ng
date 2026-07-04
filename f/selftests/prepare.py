# SPDX-License-Identifier: copyleft-next-0.3.1
"""Lay the built kselftest install tree onto the guest's `selftests` share.

Copies the published Nix store tree `f/selftests/discover` resolved to
`<share>/<kver>/tree/`, the writable directory the guest's
`kselftest@<instance>.service` executes (`WorkingDirectory=.../%v/tree`;
tests chdir into their collection dir and write there, so the read-only store
path cannot be executed in place). When the copied tree's `VERSION` and
`kselftest-list.txt` already match the store's, the copy is skipped; otherwise
any stale tree is removed and replaced, and the copy is opened for writing
(`chmod --recursive u+w`: store modes are read-only). The host never contacts
the guest.

Equivalent commands:

    rm --recursive --force "$WORKERS_DIR/shared/selftests/<vm>/<kver>/tree"
    mkdir --parents        "$WORKERS_DIR/shared/selftests/<vm>/<kver>/tree"
    cp --archive <store>/. "$WORKERS_DIR/shared/selftests/<vm>/<kver>/tree"
    chmod --recursive u+w  "$WORKERS_DIR/shared/selftests/<vm>/<kver>/tree"
"""

from __future__ import annotations

import shutil
from pathlib import Path

from f.common.devshell import run_logged
from f.common.remote import list_vms as _list_vms
from f.selftests.common import share_dir


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def _same_tree(src: Path, dest: Path) -> bool:
    """Whether `dest` already carries `src`'s tree: its `VERSION` and
    `kselftest-list.txt` both present and byte-identical to the store's."""
    for name in ("VERSION", "kselftest-list.txt"):
        s, d = src / name, dest / name
        if not d.is_file() or not s.is_file() or s.read_text() != d.read_text():
            return False
    return True


def main(vm_name: str, kernel_version: str, store_path: str) -> dict:
    src = Path(store_path)
    dest = share_dir(vm_name) / kernel_version / "tree"

    if _same_tree(src, dest):
        print(
            f"+ reusing {dest} (VERSION + kselftest-list.txt match {src})",
            flush=True,
        )
        return {"vm": vm_name, "tree": str(dest), "reused": True}

    if dest.exists():
        shutil.rmtree(dest)
        print(f"+ removed stale {dest}", flush=True)
    dest.mkdir(parents=True)
    run_logged(["cp", "--archive", f"{src}/.", str(dest)])
    # Store modes are read-only; the tests write into their collection dirs.
    run_logged(["chmod", "--recursive", "u+w", str(dest)])
    print(f"+ copied {src} -> {dest}", flush=True)
    return {"vm": vm_name, "tree": str(dest), "reused": False}
