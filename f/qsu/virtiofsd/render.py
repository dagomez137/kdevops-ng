# SPDX-License-Identifier: copyleft-next-0.3.1
"""Render the virtiofsd component: host-wide template + per-share env/drop-in.

Ports the virtiofsd slice of the qsu role (render-units.yml's host-wide
`virtiofsd@.service`/`virtiofsd@.socket`, written once, and render-per-vm.yml's
per-(VM, share) `virtiofsd@<vm>-<tag>.env` from `virtiofsd.env.j2` plus the
`virtiofsd@<vm>-<tag>.service.d/override.conf` drop-in that orders virtiofsd to stop
after the guest powerdown).

`shares` is taken verbatim when given (the boot flow wires it from the qemu-system
render result so both components agree); otherwise it is recomposed from the same
QEMU-keyword inputs. Returns the host-wide unit paths and the per-share artefacts.

Equivalent: write the rendered units into the host user-manager search path —

    ~/.config/systemd/user/virtiofsd@.service
    ~/.config/systemd/user/virtiofsd@.socket
    ~/.config/systemd/virtiofsd/<vm>-<tag>.env
    ~/.config/systemd/user/virtiofsd@<vm>-<tag>.service.d/override.conf
"""

from __future__ import annotations

import os
from pathlib import Path

from f.qsu.common import build_vars, render, systemd_config, write_unit


def main(
    vm_name: str,
    shares: list | None = None,
    custom_virtiofsd: bool = False,
    virtiofsd_binary: str = "",
    machine_type: str = "q35",
    modules_dir: str = "",
    controller_share: bool = False,
    controller_share_tag: str = "controller-share",
    controller_share_dir: str = "",
    controller_share_guest_mount: str = "",
    controller_share_readwrite: bool = False,
    kernel: dict | None = None,
    closure: dict | None = None,
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    fi = {
        "vm_name": vm_name, "machine_type": machine_type,
        "custom_virtiofsd": custom_virtiofsd,
        "virtiofsd_binary": virtiofsd_binary or None, "modules_dir": modules_dir or None,
        "shares": shares, "controller_share": controller_share,
        "controller_share_tag": controller_share_tag,
        "controller_share_dir": controller_share_dir or None,
        "controller_share_guest_mount": controller_share_guest_mount or None,
        "controller_share_readwrite": controller_share_readwrite,
    }
    v = build_vars(fi, kernel=kernel, closure=closure, workers=workers)
    composed = v.get("shares", [])

    cfg = systemd_config()
    user = cfg / "user"
    vfsd = cfg / "virtiofsd"

    service = user / "virtiofsd@.service"
    socket = user / "virtiofsd@.socket"
    write_unit(service, render("virtiofsd@.service.j2", v, workers))
    write_unit(socket, render("virtiofsd@.socket.j2", v, workers))

    per_share = []
    for s in composed:
        tag = s["tag"]
        ctx = {**v, "share_tag": tag, "shares": [s]}
        env = vfsd / f"{vm_name}-{tag}.env"
        write_unit(env, render("virtiofsd.env.j2", ctx, workers))
        dropin = user / f"virtiofsd@{vm_name}-{tag}.service.d/override.conf"
        write_unit(dropin, render("virtiofsd-override.conf.j2", ctx, workers))
        per_share.append({"tag": tag, "env": str(env), "override": str(dropin)})

    return {
        "vm_name": vm_name,
        "service": str(service),
        "socket": str(socket),
        "shares": composed,
        "share_tags": [s["tag"] for s in composed],
        "per_share": per_share,
    }
