# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the qsu render steps' pure paths (`f/qsu/*/render`)."""

import importlib
import inspect
from pathlib import Path

import pytest
import yaml

from f.qsu import common

cloud_init = importlib.import_module("f.qsu.cloud-init.render")
qemu_system = importlib.import_module("f.qsu.qemu-system.render")
vfio = importlib.import_module("f.qsu.vfio.render")

# A flag that stops anywhere along the chain below gives a guest that mounts a
# tag nobody serves, and it drops to emergency mode on `tag not found`.
SUITE_SHARE_FLAGS = ("fstests", "selftests", "usertests", "blktests")


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


@pytest.mark.parametrize("flag", SUITE_SHARE_FLAGS)
def test_every_suite_share_flag_is_a_render_parameter(flag):
    assert flag in inspect.signature(qemu_system.main).parameters


@pytest.mark.parametrize("flag", SUITE_SHARE_FLAGS)
def test_every_suite_share_flag_reaches_render_from_the_boot_flow(flag):
    flow = Path("f/qsu/boot.flow/flow.yaml").read_text()
    assert f"expr: flow_input.sharing?.{flag}" in flow


@pytest.mark.parametrize("flag", SUITE_SHARE_FLAGS)
def test_every_suite_share_flag_is_derived_by_the_bringup_flow(flag):
    flow = Path("f/qsu/bringup.flow/flow.yaml").read_text()
    assert (
        f'{flag}: (flow_input.closure?.closure?.test_suites || []).includes("{flag}")'
        in flow
    )


@pytest.mark.parametrize("flag", SUITE_SHARE_FLAGS)
def test_every_suite_share_tag_is_canonical_so_destroy_cleans_it(flag):
    assert flag in common.CANONICAL_SHARE_TAGS


# The one NVMe default not inherited from QEMU: at QEMU's false every doorbell
# write exits to userspace on the vCPU thread, a fixed cost per command.
def test_the_doorbell_eventfd_is_on_by_default():
    assert inspect.signature(qemu_system.main).parameters["ioeventfd"].default == "on"


def test_ioeventfd_reaches_render_from_the_boot_flow():
    flow = Path("f/qsu/boot.flow/flow.yaml").read_text()
    assert "expr: flow_input.nvme?.ioeventfd" in flow


# Every drive knob the form offers has to reach the render step, which is the
# only thing that turns one into a QEMU flag. A knob that stops at the flow is
# a field an operator sets and a guest never sees.
def test_every_nvme_knob_the_form_offers_reaches_the_render_step():
    flow = yaml.safe_load(Path("f/qsu/boot.flow/flow.yaml").read_text())
    render = next(
        m for m in flow["value"]["modules"] if m["id"] == "render_qemu_system"
    )
    exprs = " ".join(
        t["expr"] for t in render["value"]["input_transforms"].values() if "expr" in t
    )
    create = next(m for m in flow["value"]["modules"] if m["id"] == "create_nvme")
    create_exprs = " ".join(
        t["expr"] for t in create["value"]["input_transforms"].values() if "expr" in t
    )
    for knob in flow["schema"]["properties"]["nvme"]["properties"]:
        if knob == "customize_drives":
            continue  # a form gate, not a QEMU flag
        assert (
            f"flow_input.nvme?.{knob}" in exprs
            or f"flow_input.nvme?.{knob}" in create_exprs
        ), f"{knob} reaches neither the render step nor create_nvme"


def test_extra_qemu_args_reach_the_render_step():
    assert "extra_qemu_args" in inspect.signature(qemu_system.main).parameters


def test_extra_qemu_args_reach_render_from_the_boot_flow():
    flow = Path("f/qsu/boot.flow/flow.yaml").read_text()
    assert "expr: flow_input.qemu?.extra_qemu_args" in flow


def test_extra_qemu_args_are_offered_by_the_bringup_form():
    flow = Path("f/qsu/bringup.flow/flow.yaml").read_text()
    assert "extra_qemu_args" in flow


# A group the generator forwards key by key drops any knob its list forgot:
# the form still shows it, the run still succeeds, the value goes nowhere.
@pytest.mark.parametrize("group", ["boot_qemu", "boot_kernel"])
def test_every_boot_knob_the_form_offers_reaches_the_boot_subflow(group):
    flow = yaml.safe_load(Path("f/qsu/bringup.flow/flow.yaml").read_text())
    boot = next(m for m in flow["value"]["modules"] if m["id"] == "boot")
    exprs = " ".join(
        t["expr"] for t in boot["value"]["input_transforms"].values() if "expr" in t
    )
    if f"...flow_input.{group}" in exprs:
        return  # spread forwards the whole group, knob by knob is moot
    for knob in flow["schema"]["properties"][group]["properties"]:
        assert f"flow_input.{group}?.{knob}" in exprs, (
            f"{group}.{knob} is on the form but no transform carries it into "
            f"f/qsu/boot, so a value set there is silently dropped"
        )
