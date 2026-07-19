# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the qsu render steps' pure paths (`f/qsu/*/render`)."""

import importlib

import pytest

cloud_init = importlib.import_module("f.qsu.cloud-init.render")
qemu_system = importlib.import_module("f.qsu.qemu-system.render")
vfio = importlib.import_module("f.qsu.vfio.render")


def test_cloud_init_render_is_a_deferred_scaffold():
    assert cloud_init.main("demo") == {"deferred": True}


def test_vfio_render_is_a_deferred_scaffold():
    assert vfio.main("demo") == {"deferred": True}


def test_list_iommu_falls_back_on_an_unresolvable_qemu():
    options = qemu_system.list_iommu(qemu_source="qemu-build", qemu_binary="")
    assert options[0] == {"label": "none", "value": ""}
    assert {o["value"] for o in options[1:]} == {
        "intel-iommu",
        "amd-iommu",
        "virtio-iommu-pci",
        "arm-smmuv3",
    }


def test_render_rejects_a_kernel_image_without_its_modules(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="set together"):
        qemu_system.main(vm_name="demo", auto_vm_name=False, kernel_image="/boot/bz")


def test_render_rejects_modules_without_a_kernel_image(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="set together"):
        qemu_system.main(vm_name="demo", auto_vm_name=False, modules_dir="/m")


def test_render_refuses_a_kernelless_vm(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    monkeypatch.delenv("WM_ROOT_FLOW_JOB_ID", raising=False)
    with pytest.raises(ValueError, match="no kernel image resolved"):
        qemu_system.main(
            vm_name="demo",
            auto_vm_name=False,
            qemu_source="qemu-build",
            qemu_binary="/b/bin/qemu-system-x86_64",
            custom_virtiofsd=True,
            virtiofsd_binary="/b/bin/virtiofsd",
            nvme_drive_count=0,
        )
