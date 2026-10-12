# SPDX-License-Identifier: copyleft-next-0.3.1
"""Host PCI devices for VFIO passthrough: the picker, the checks, the var.

Library module (not a step). Reads the host's /sys/bus/pci, which the vm worker
renders on. `options` feeds the `dynmultiselect-list_pci_devices` picker;
`passthrough` turns the picked addresses into the vendored `pci_passthrough`
var (vendor/qemu-system-units/docs/vars.md, "PCIe passthrough") after checking
what QEMU would otherwise fail on at VM start: that the device exists, that the
host IOMMU put it in a group, that every endpoint in the group is passed
through with it, and that the one-time VFIO setup bound it to vfio-pci and lets
this user open its group.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SYSFS = Path("/sys/bus/pci/devices")
DEV_VFIO = Path("/dev/vfio")

REQUIREMENTS = "docs/getting-started/requirements.rst (PCI passthrough (VFIO))"

_ADDR = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
# The opts land in the per-VM EnvironmentFile, which splits on whitespace and
# parses quotes and backslashes.
_OPTS_REFUSED = re.compile(r"[\s\"'\\\x00-\x1f\x7f]")

# The classes a passthrough picker is about, by PCI class and subclass; the
# rest show their numeric class. Bridges share a group with the devices behind
# them without being passed through.
_CLASSES = {
    0x0108: "NVMe controller",
    0x0200: "Ethernet controller",
    0x0207: "InfiniBand controller",
    0x0300: "VGA controller",
    0x0302: "3D controller",
    0x0380: "display controller",
    0x0403: "audio device",
    0x0C03: "USB controller",
    0x1200: "processing accelerator",
}
_GPU_CLASSES = {0x0300, 0x0302, 0x0380, 0x1200}
_BRIDGE_CLASS = 0x06
_VENDORS = {
    0x1002: "AMD",
    0x1022: "AMD",
    0x10DE: "NVIDIA",
    0x144D: "Samsung",
    0x15B3: "Mellanox",
    0x8086: "Intel",
}


def normalize(addr) -> str:
    """`81:00.0` as `0000:81:00.0`, lower-cased; the form lspci -D prints."""
    if not isinstance(addr, str):
        raise ValueError(f"{addr!r} is not a PCI address string such as 0000:81:00.0")
    addr = addr.strip().lower()
    return addr if addr.count(":") == 2 else f"0000:{addr}"


def _link_name(path: Path) -> str | None:
    return os.path.basename(os.readlink(path)) if path.is_symlink() else None


def device(addr: str, sysfs: Path | None = None) -> dict:
    """What sysfs says about one PCI function."""
    d = (sysfs or SYSFS) / addr
    cls = int((d / "class").read_text(), 16) >> 8
    return {
        "addr": addr,
        "vendor": int((d / "vendor").read_text(), 16),
        "device": int((d / "device").read_text(), 16),
        "class": cls,
        "driver": _link_name(d / "driver"),
        "group": _link_name(d / "iommu_group"),
    }


def devices(sysfs: Path | None = None) -> list[dict]:
    sysfs = sysfs or SYSFS
    if not sysfs.is_dir():
        return []
    return [device(p.name, sysfs) for p in sorted(sysfs.iterdir())]


def label(dev: dict) -> str:
    vendor = _VENDORS.get(dev["vendor"], f"{dev['vendor']:04x}")
    kind = _CLASSES.get(dev["class"], f"class {dev['class']:04x}")
    group = f"IOMMU group {dev['group']}" if dev["group"] else "no IOMMU group"
    return (
        f"{dev['addr']}  {vendor} {kind} [{dev['vendor']:04x}:{dev['device']:04x}]"
        f"  {group}  {dev['driver'] or 'no driver'}"
    )


def options(filterText: str = "", sysfs: Path | None = None) -> list[dict]:
    """Picker entries, GPUs and accelerators first; bridges left out."""
    devs = [d for d in devices(sysfs) if d["class"] >> 8 != _BRIDGE_CLASS]
    devs.sort(key=lambda d: (d["class"] not in _GPU_CLASSES, d["addr"]))
    out = [{"label": label(d), "value": d["addr"]} for d in devs]
    needle = filterText.lower()
    return [o for o in out if needle in o["label"].lower()]


def passthrough(
    addrs,
    opts: str = "",
    sysfs: Path | None = None,
    check_access: bool = True,
    dev: Path | None = None,
) -> list[dict]:
    """The `pci_passthrough` var for `addrs`, or ValueError naming what is wrong.

    `check_access` off skips the checks the one-time root setup satisfies.
    """
    sysfs = sysfs or SYSFS
    if isinstance(addrs, str):
        addrs = addrs.replace("\n", ",").split(",")
    picked = [normalize(a) for a in addrs or [] if not isinstance(a, str) or a.strip()]
    if len(set(picked)) != len(picked):
        raise ValueError(f"PCI address listed twice: {picked}")
    for addr in picked:
        if not _ADDR.match(addr):
            raise ValueError(
                f"{addr!r} is not a PCI address; expected domain:bus:slot.function "
                "such as 0000:81:00.0 (`lspci -D`)"
            )
        if not (sysfs / addr).is_dir():
            raise ValueError(f"no PCI device {addr} on this host")
        group = _link_name(sysfs / addr / "iommu_group")
        if not group:
            raise ValueError(
                f"{addr} has no IOMMU group: enable the IOMMU in the firmware, "
                "and with intel_iommu=on on an Intel host whose kernel leaves it "
                f"off; see {REQUIREMENTS}"
            )
        peers = sysfs / addr / "iommu_group/devices"
        for peer in sorted(p.name for p in peers.iterdir()) if peers.is_dir() else []:
            if (
                peer not in picked
                and device(peer, sysfs)["class"] >> 8 != _BRIDGE_CLASS
            ):
                raise ValueError(
                    f"{addr} shares IOMMU group {group} with {peer}; VFIO takes a "
                    "whole group, so pass every endpoint in it through; see "
                    f"{REQUIREMENTS}"
                )
    if picked and check_access:
        _check_vfio(sysfs)
        for addr in picked:
            _check_bound(addr, sysfs, dev or DEV_VFIO)
    opts = (opts or "").strip()
    if bad := _OPTS_REFUSED.search(opts):
        raise ValueError(
            f"PCI passthrough options {opts!r} carry {bad.group()!r}; give "
            "comma-separated QEMU vfio-pci properties such as rombar=0,x-vga=on "
            "with no whitespace, quotes or backslashes"
        )
    return [{"addr": a, **({"opts": opts} if opts else {})} for a in picked]


def _check_vfio(sysfs: Path) -> None:
    if not (sysfs.parent / "drivers/vfio-pci").is_dir():
        raise ValueError(
            f"the vfio-pci driver is not loaded (no {sysfs.parent}/drivers/vfio-pci); "
            f"load the vfio-pci module as {REQUIREMENTS} describes"
        )


def _check_bound(addr: str, sysfs: Path, dev: Path) -> None:
    """Refuse a device the root setup left on its host driver, or whose VFIO
    group node this user cannot open."""
    driver = _link_name(sysfs / addr / "driver")
    if driver != "vfio-pci":
        raise ValueError(
            f"{addr} is bound to {driver or 'no driver'}, not vfio-pci; the udev "
            "rule from the one-time VFIO setup binds it, from the next boot or "
            f"after the rebind {REQUIREMENTS} gives"
        )
    node = dev / str(_link_name(sysfs / addr / "iommu_group"))
    if not os.access(node, os.R_OK | os.W_OK):
        raise ValueError(
            f"cannot open {node}: this user must be in the kvm group, and its "
            "user manager must have restarted since joining it (log out of every "
            f"session, or reboot); see {REQUIREMENTS}"
        )


def memlock_needed(ram_mb: int) -> int:
    """Bytes the VM must lock: all of its RAM, as the templates' LimitMEMLOCK sets."""
    return (int(ram_mb) + 256) * 1024 * 1024


def main():
    """Library module imported by the f/qsu/* steps; not a runnable step."""
    return "f/qsu/pci: host PCI devices for VFIO passthrough"
