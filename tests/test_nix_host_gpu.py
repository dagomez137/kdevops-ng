# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for `f.nix.render_config`'s host NVIDIA GPU detection."""

from pathlib import Path

import pytest

from f.nix import render_config
from f.qsu import pci


def _sysfs(tmp_path: Path, devices: dict[str, tuple[int, int]]) -> Path:
    """A /sys/bus/pci/devices tree: devices {addr: (vendor, class)}."""
    devs = tmp_path / "devices"
    for addr, (vendor, cls) in devices.items():
        d = devs / addr
        d.mkdir(parents=True)
        (d / "vendor").write_text(f"0x{vendor:04x}\n")
        (d / "device").write_text("0x0000\n")
        (d / "class").write_text(f"0x{cls:06x}\n")
    return devs


@pytest.mark.parametrize(
    "devices, found",
    [
        ({"0000:81:00.0": (0x10DE, 0x030200)}, True),
        ({"0000:81:00.0": (0x10DE, 0x030000)}, True),
        ({"0000:81:00.0": (0x10DE, 0x120000)}, True),
        ({"0000:81:00.1": (0x10DE, 0x040300)}, False),
        ({"0000:00:01.0": (0x10DE, 0x060400)}, False),
        ({"0000:03:00.0": (0x1002, 0x030000)}, False),
        ({}, False),
    ],
)
def test_host_nvidia_gpu_filters_vendor_and_class(
    monkeypatch, tmp_path, devices, found
):
    monkeypatch.setattr(pci, "SYSFS", _sysfs(tmp_path, devices))
    assert render_config._host_nvidia_gpu() is found


def test_host_nvidia_gpu_without_sysfs(monkeypatch, tmp_path):
    monkeypatch.setattr(pci, "SYSFS", tmp_path / "absent")
    assert render_config._host_nvidia_gpu() is False
