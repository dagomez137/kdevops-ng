# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the qemu/virtiofsd binary selection (`f.qsu.binaries`)."""

from pathlib import Path

import pytest

from f.qsu import binaries


def test_main_names_the_library():
    assert "f/qsu/binaries" in binaries.main()


def test_flake_is_a_path_ref_into_the_vendor_tree(monkeypatch):
    monkeypatch.setenv("VENDOR_DIR", "/v")
    assert binaries._flake() == "path:/v/nixos-flake"


def test_flake_derives_vendor_from_the_workers_root(monkeypatch):
    monkeypatch.delenv("VENDOR_DIR", raising=False)
    assert binaries._flake(Path("/wb/workers")) == "path:/vendor/nixos-flake"


def test_qemu_bindir_is_the_binary_parent():
    path = "/nix/store/abc-qemu/bin/qemu-system-x86_64"
    assert binaries.qemu_bindir(path) == "/nix/store/abc-qemu/bin"


def test_resolve_qemu_binary_qemu_build_returns_the_operator_binary():
    fi = {"qemu_source": "qemu-build", "qemu_binary": "/b/bin/qemu-system-x86_64"}
    assert binaries.resolve_qemu_binary(fi) == "/b/bin/qemu-system-x86_64"


def test_resolve_qemu_binary_qemu_build_without_a_binary_raises():
    with pytest.raises(ValueError, match="qemu_binary is empty"):
        binaries.resolve_qemu_binary({"qemu_source": "qemu-build"})


def test_resolve_virtiofsd_binary_custom_returns_the_operator_binary():
    fi = {"custom_virtiofsd": True, "virtiofsd_binary": "/b/bin/virtiofsd"}
    assert binaries.resolve_virtiofsd_binary(fi) == "/b/bin/virtiofsd"


def test_iommu_options_fall_back_to_the_full_supported_set():
    options = binaries.iommu_options({"qemu_source": "qemu-build"})
    assert options[0] == {"label": "none", "value": ""}
    assert [o["value"] for o in options[1:]] == list(binaries.SUPPORTED_IOMMU)


def test_iommu_options_filter_matches_the_label():
    options = binaries.iommu_options({"qemu_source": "qemu-build"}, "intel")
    assert options == [{"label": "intel-iommu", "value": "intel-iommu"}]


def test_iommu_options_filter_keeps_the_none_entry():
    options = binaries.iommu_options({"qemu_source": "qemu-build"}, "none")
    assert options == [{"label": "none", "value": ""}]
