# SPDX-License-Identifier: copyleft-next-0.3.1
"""Render the qemu-system component: host-wide template + per-VM env/drop-in.

Ports the qsu role's render-units.yml (the host-wide `qemu-system@.service` and the
`qmp-powerdown` ExecStop helper, written once into `~/.config/systemd/user` /
`~/.config/systemd/qemu-system`) and the qemu-system slice of render-per-vm.yml (the
per-VM `<vm>.env` from `vm.env.j2`, which imports the `nvme.env.j2` macros, plus the
`qemu-system@<vm>.service.d/override.conf` drop-in).

Inputs are upstream QEMU flag names; the kernel/closure build manifests supply
`-kernel`/`-initrd`/`-append` and the `/lib/modules` share. Returns the written
paths, the composed `shares` (boot restarts their sockets) and `ssh_port`/`vsock_cid`.

Equivalent: write the rendered units into the host user-manager search path:

    ~/.config/systemd/user/qemu-system@.service
    ~/.config/systemd/qemu-system/<vm>.env
    ~/.config/systemd/user/qemu-system@<vm>.service.d/override.conf
    ~/.config/systemd/qemu-system/qmp-powerdown
    $WORKERS_DIR/shared/vm/<vm>.vars.json    # deployed-VM registry + closure reuse for f/qsu/resolve
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from f.qsu.binaries import iommu_options
from f.qsu.common import (
    build_vars,
    emit_vars_yaml,
    qsu_dir,
    render,
    resolve_vm_name,
    systemd_config,
    write_unit,
)


def list_iommu(
    filterText: str = "",
    qemu_source: str = "nixpkgs",
    qemu_binary: str = "",
    **_: object,
) -> list[dict]:
    """`dynselect-list_iommu` entrypoint for `iommu`: see `f.qsu.binaries.iommu_options`.

    Queries the same qemu the render will use (`qemu_source`/`qemu_binary` sit beside
    `iommu` in this schema), so the dropdown reflects that exact binary's vIOMMUs.
    """
    return iommu_options(
        {"qemu_source": qemu_source, "qemu_binary": qemu_binary}, filterText
    )


def main(
    vm_name: str,
    auto_vm_name: bool = True,
    cpu: str = "host",
    accel: str = "kvm",
    ram: int = 4096,
    cpus: int = 4,
    machine_type: str = "q35",
    ssh_port: int | None = None,
    vsock_cid: int | None = None,
    ssh_port_base: int = 10022,
    vsock_cid_base: int = 100,
    vm_index: int = 0,
    iommu: str = "",
    qemu_source: str = "nixpkgs",
    qemu_binary: str = "",
    custom_virtiofsd: bool = False,
    virtiofsd_binary: str = "",
    kernel_image: str = "",
    kernel_initrd: str = "",
    kernel_append: str = "",
    modules_dir: str = "",
    shares: list | None = None,
    fstests: bool = False,
    home_share: bool = False,
    home_share_readwrite: bool = False,
    controller_share: bool = False,
    controller_share_tag: str = "controller-share",
    controller_share_dir: str = "",
    controller_share_guest_mount: str = "",
    controller_share_readwrite: bool = False,
    nvme_drive_count: int = 4,
    # Per-drive NVMe knobs (single value or per-drive comma-list); 4kn defaults on
    # the BlockConf sizes, the rest empty/false. See NVME_*_KNOBS in f/qsu/common.
    logical_block_size: str = "4096",
    physical_block_size: str = "4096",
    min_io_size: str = "4096",
    opt_io_size: str = "4096",
    discard_granularity: str = "4096",
    write_cache: str = "",
    mdts: str = "",
    cmb_size_mb: str = "",
    legacy_cmb: bool = False,
    pmr_size: str = "",
    pmr_share: bool = True,
    pmr_pmem: bool = False,
    atomic_dn: bool = False,
    atomic_awun: str = "",
    atomic_awupf: str = "",
    atomic_nawun: str = "",
    atomic_nawupf: str = "",
    atomic_nabsn: str = "",
    atomic_nabspf: str = "",
    atomic_nabo: str = "",
    kernel: dict | None = None,
    closure: dict | None = None,
    emit_vars_yaml_snapshot: bool = False,
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    # Resolve the VM name once here (the first step); downstream steps take this
    # resolved name from results.render_qemu_system.vm_name, so the whole flow agrees.
    vm_name = resolve_vm_name({"auto_vm_name": auto_vm_name, "vm_name": vm_name})
    fi = {
        "vm_name": vm_name,
        "cpu": cpu,
        "accel": accel,
        "ram": ram,
        "cpus": cpus,
        "machine_type": machine_type,
        "vm_index": vm_index,
        "ssh_port": ssh_port,
        "vsock_cid": vsock_cid,
        "ssh_port_base": ssh_port_base,
        "vsock_cid_base": vsock_cid_base,
        "iommu": iommu or None,
        "qemu_source": qemu_source,
        "qemu_binary": qemu_binary or None,
        "custom_virtiofsd": custom_virtiofsd,
        "virtiofsd_binary": virtiofsd_binary or None,
        "kernel_image": kernel_image or None,
        "kernel_initrd": kernel_initrd or None,
        "kernel_append": kernel_append or None,
        "modules_dir": modules_dir or None,
        "shares": shares,
        "fstests": fstests,
        "home_share": home_share,
        "home_share_readwrite": home_share_readwrite,
        "controller_share": controller_share,
        "controller_share_tag": controller_share_tag,
        "controller_share_dir": controller_share_dir or None,
        "controller_share_guest_mount": controller_share_guest_mount or None,
        "controller_share_readwrite": controller_share_readwrite,
        "nvme_drive_count": nvme_drive_count,
        "logical_block_size": logical_block_size,
        "physical_block_size": physical_block_size,
        "min_io_size": min_io_size,
        "opt_io_size": opt_io_size,
        "discard_granularity": discard_granularity,
        "write_cache": write_cache,
        "mdts": mdts,
        "cmb_size_mb": cmb_size_mb,
        "legacy_cmb": legacy_cmb,
        "pmr_size": pmr_size,
        "pmr_share": pmr_share,
        "pmr_pmem": pmr_pmem,
        "atomic_dn": atomic_dn,
        "atomic_awun": atomic_awun,
        "atomic_awupf": atomic_awupf,
        "atomic_nawun": atomic_nawun,
        "atomic_nawupf": atomic_nawupf,
        "atomic_nabsn": atomic_nabsn,
        "atomic_nabspf": atomic_nabspf,
        "atomic_nabo": atomic_nabo,
    }
    if bool(kernel_image) != bool(modules_dir):
        raise ValueError(
            "kernel_image and modules_dir must be set together (the kernel and its "
            "/lib/modules are a unit): supply both to override the kernel explicitly, or "
            "neither to take both from the build/reuse manifest"
        )
    v = build_vars(fi, kernel=kernel, closure=closure, workers=workers)
    if "kernel" not in v:
        raise ValueError(
            "no kernel image resolved: a `reuse` component needs a Reuse-from-VM with a "
            "sidecar or an explicit kernel_image; a `build` needs the build result. "
            "Refusing to render a kernelless VM."
        )

    cfg = systemd_config()
    user = cfg / "user"
    qsys = cfg / "qemu-system"

    service = user / "qemu-system@.service"
    write_unit(service, render("qemu-system@.service.j2", v, workers))

    powerdown = qsys / "qmp-powerdown"
    powerdown.parent.mkdir(parents=True, exist_ok=True)
    src = qsu_dir(workers) / "files/qmp-powerdown"
    shutil.copyfile(src, powerdown)
    print(f"copied {src} -> {powerdown}", flush=True)

    env = qsys / f"{vm_name}.env"
    write_unit(env, render("vm.env.j2", v, workers))

    dropin = user / f"qemu-system@{vm_name}.service.d/override.conf"
    write_unit(dropin, render("qemu-system-override.conf.j2", v, workers))

    # Reuse sidecar: the deployed-VM registry (the bringup refresh-VM dropdown) and the
    # closure init/initrd a refresh reuses (f/qsu/resolve reads it). Lives under
    # WORKERS_DIR/shared (every worker + the host can read it), unlike the systemd
    # config dir which only the vm worker mounts.
    sidecar = workers / "shared/vm" / f"{vm_name}.vars.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    # Record the built binary only for qemu-build: a nixpkgs path is a GC-able store
    # path, and reusing a nixpkgs VM should re-resolve nixpkgs, not pin that path.
    data = (
        json.dumps(
            {
                "vm_name": vm_name,
                "kernel": kernel or {},
                "closure": closure or {},
                "qemu_binary": v["qemu_binary"]
                if qemu_source == "qemu-build"
                else None,
                "qemu_source": qemu_source,
                # The host↔guest virtiofs-share contract (qsu side here mirrors the closure's
                # fstab). Recorded so a reuse reconfigure replays the SAME host shares the
                # reused closure still mounts; else the guest drops to emergency mode on a
                # `tag not found`. modules_dir is omitted: it tracks the kernel, not the closure.
                "sharing": {
                    "fstests": fstests,
                    "home_share": home_share,
                    "home_share_readwrite": home_share_readwrite,
                    "shares": shares or [],
                    "controller_share": controller_share,
                    "controller_share_tag": controller_share_tag,
                    "controller_share_dir": controller_share_dir or None,
                    "controller_share_guest_mount": controller_share_guest_mount
                    or None,
                    "controller_share_readwrite": controller_share_readwrite,
                },
            },
            indent=2,
        )
        + "\n"
    )
    # Atomic: a concurrent reader (resolve, or the refresh-VM dropdown's *.vars.json glob) never
    # sees a half-written sidecar.
    tmp = sidecar.parent / (sidecar.name + ".tmp")
    tmp.write_text(data)
    os.replace(tmp, sidecar)
    print(f"+ wrote {sidecar}", flush=True)

    out = {
        "vm_name": vm_name,
        "service": str(service),
        "env": str(env),
        "override": str(dropin),
        "qmp_powerdown": str(powerdown),
        "shares": v.get("shares", []),
        "ssh_port": v["ssh_port"],
        "vsock_cid": v["vsock_cid"],
        "vars_sidecar": str(sidecar),
    }
    if emit_vars_yaml_snapshot:
        out["vars_yaml"] = emit_vars_yaml(vm_name, v)
    return out
