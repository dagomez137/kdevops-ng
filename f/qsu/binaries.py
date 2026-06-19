# SPDX-License-Identifier: copyleft-next-0.3.1
"""Resolve /nix/store paths and qemu/virtiofsd binaries for the qsu steps.

Library module imported as `f.qsu.binaries`; not a runnable step. Touches only
the standard library and `f.common.devshell`, so it stays importable from a
dynselect runtime (which `f.qsu.common`, with its jinja2/yaml imports, is not).
`iommu_options` backs the `dynselect-list_iommu` dropdown.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from f.common.devshell import Nix, run_logged


def main():
    """Library module imported by the f/qsu/* steps; not a runnable step."""
    return "f/qsu/binaries: /nix store-path and qemu/virtiofsd binary resolution"


def _workers() -> Path:
    return Path(os.environ["WORKERS_DIR"])


def _flake(workers: Path | None = None) -> str:
    # path: resolves the subtree as a standalone flake, so store_out's #qemu/
    # #virtiofsd/#socat stay content-addressed by the subtree alone, not by the
    # enclosing kdevops-ng git rev (which would re-copy the repo and, when dirty,
    # churn the resolved paths the rendered units embed).
    return f"path:{(workers or _workers())}/shared/nixos-flake"


# --- nix store-path resolution -------------------------------------------------
# Units fork on the host, so every binary an ExecStart= touches must be a
# /nix/store path (valid identically on host and worker; /nix is shared). qemu,
# virtiofsd and socat are exposed as flake packages so a build resolves them.
def store_out(attr: str, workers: Path | None = None) -> str:
    """Resolve a nixos-flake package to its /nix/store output path."""
    return Nix().out_path(f"{_flake(workers)}#{attr}")


def qemu_bindir(qemu_binary: str) -> str:
    """The bin/ dir holding the VM's qemu — qemu-img must come from here too."""
    return str(Path(qemu_binary).parent)


def resolve_qemu_binary(fi: dict, workers: Path | None = None) -> str:
    """Pick the `qemu-system-*` binary by `qemu_source`.

    `nixpkgs` (default) = the `qemu` package from nixpkgs, provided by the vendored
    nixos-flake (what the NixOS Build uses); `qemu-build` = the operator's `qemu_binary`
    (a binary from `f/qemu/build`). qemu-img is always the sibling of whatever this returns.
    """
    if fi.get("qemu_source", "nixpkgs") == "qemu-build":
        if not fi.get("qemu_binary"):
            raise ValueError(
                "qemu_source is qemu-build but no qemu_binary — reuse needs a Reuse from VM "
                "whose sidecar has a built qemu; build supplies it from the build result"
            )
        return fi["qemu_binary"]
    return f"{store_out('qemu', workers)}/bin/qemu-system-x86_64"


def resolve_virtiofsd_binary(fi: dict, workers: Path | None = None) -> str:
    """Pick the `virtiofsd` binary by `custom_virtiofsd`.

    Off (default) = the reproducible nixos-flake `virtiofsd` store path; on = the
    operator's `virtiofsd_binary` path (a nix output or a custom build).
    """
    if fi.get("custom_virtiofsd") and fi.get("virtiofsd_binary"):
        return fi["virtiofsd_binary"]
    return f"{store_out('virtiofsd', workers)}/bin/virtiofsd"


# The vIOMMU device names vm.env.j2 renders, in the order it lists them.
SUPPORTED_IOMMU = ("intel-iommu", "amd-iommu", "virtio-iommu-pci", "arm-smmuv3")
_IOMMU_DEVICE = re.compile(r'^name "([^"]+)"')


def iommu_options(fi: dict, filter_text: str = "") -> list[dict]:
    """vIOMMU choices for a `dynselect-` dropdown, as `[{label, value}]`.

    Queries the operator's qemu (`resolve_qemu_binary`) with `-device help` and keeps
    only the devices both that binary reports and the template renders
    (`SUPPORTED_IOMMU`). A leading `none` (value "") is always offered. Never raises —
    an unresolvable qemu falls back to the full supported set so the form still works.
    """
    try:
        out = run_logged([resolve_qemu_binary(fi), "-device", "help"],
                         capture=True, check=False)
        found = {m.group(1) for line in out.splitlines() if (m := _IOMMU_DEVICE.match(line))}
        devices = [d for d in SUPPORTED_IOMMU if d in found]
    except Exception:
        devices = list(SUPPORTED_IOMMU)
    options = [{"label": "none", "value": ""}]
    options += [{"label": d, "value": d} for d in devices]
    return [o for o in options if filter_text.lower() in o["label"].lower()]
