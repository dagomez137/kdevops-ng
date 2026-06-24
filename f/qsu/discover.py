# SPDX-License-Identifier: copyleft-next-0.3.1
"""Discover a deployed VM's reuse artifacts from its render sidecar.

`f/qsu/qemu-system/render` writes `WORKERS_DIR/shared/vm/<vm>.vars.json` recording the
kernel + closure manifests and the QEMU binary a VM booted with. This reads it back so
a reconfigure (`f/qsu/bringup` with a component's source set to `reuse`) feeds those
artifacts to the boot step instead of rebuilding. The sidecar lives under
`WORKERS_DIR/shared` (every worker reads it), so this runs on any worker. A missing
sidecar returns empty manifests; the caller then falls back to explicit reuse paths.

Equivalent command:

    cat "$WORKERS_DIR/shared/vm/<vm_name>.vars.json"
"""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path


def host_operator() -> str:
    """The host operator's username, for the guest's `/home/<user>` (root's home).

    The worker mounts the operator's home at `$HOME` (e.g. /home/dagomez), so the
    basename is their name. `getpass`/`getuid` see the in-namespace identity (root) in a
    rootless worker, so they're unreliable; a non-root default otherwise."""
    home = os.environ.get("HOME", "").rstrip("/")
    if home.startswith("/home/"):  # rstrip first, so a bare "/home/" falls through
        name = os.path.basename(home)
    else:
        # getpass.getuser() raises when no username env var is set AND the (in-namespace)
        # uid has no passwd entry: exactly the rootless-worker case. Degrade, don't crash.
        try:
            name = getpass.getuser()
        except Exception:
            name = ""
    return name if name and name not in (".", "..", "root") else "kdevops"


def main(vm_name: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    host = {"host_user": host_operator()}
    sidecar = workers / "shared/vm" / f"{vm_name}.vars.json"
    if not sidecar.is_file():
        print(f"no reuse sidecar at {sidecar}", flush=True)
        return {
            "vm_name": vm_name,
            "kernel": {},
            "closure": {},
            "qemu_binary": None,
            "qemu_source": None,
            "sharing": {},
            **host,
        }
    try:
        data = json.loads(sidecar.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"unreadable sidecar {sidecar}: {e}", flush=True)
        return {
            "vm_name": vm_name,
            "kernel": {},
            "closure": {},
            "qemu_binary": None,
            "qemu_source": None,
            "sharing": {},
            **host,
        }
    sharing = data.get("sharing") or {}
    print(
        f"reuse from {sidecar}: qemu_source={data.get('qemu_source')} "
        f"kernel={'set' if data.get('kernel') else 'empty'} "
        f"closure={'set' if data.get('closure') else 'empty'} "
        f"shares=fstests:{'on' if sharing.get('fstests') else 'off'},"
        f"home:{'on' if sharing.get('home_share') else 'off'} "
        f"host_user={host['host_user']}",
        flush=True,
    )
    return {
        "vm_name": vm_name,
        "kernel": data.get("kernel") or {},
        "closure": data.get("closure") or {},
        "qemu_binary": data.get("qemu_binary"),
        "qemu_source": data.get("qemu_source"),
        "sharing": sharing,
        **host,
    }
