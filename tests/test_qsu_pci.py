# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for PCI passthrough (`f.qsu.pci` and its path into the units)."""

import importlib
import os
from pathlib import Path

import pytest

from f.qsu import common, pci

REPO = Path(__file__).resolve().parent.parent

QEMU_BUILD = {
    "qemu_source": "qemu-build",
    "qemu_binary": "/builds/qemu/bin/qemu-system-x86_64",
    "custom_virtiofsd": True,
    "virtiofsd_binary": "/builds/virtiofsd/bin/virtiofsd",
}

GPU = "0000:81:00.0"
GPU_AUDIO = "0000:81:00.1"
BRIDGE = "0000:80:01.0"
NVME = "0000:01:00.0"


def _sysfs(tmp_path: Path, devices: dict, vfio: bool = True, node: bool = True) -> Path:
    """A /sys tree: devices {addr: (vendor, device, class, driver, group)}, and
    beside it a /dev/vfio node for each group a vfio-pci device is in."""
    sys = tmp_path / "sys"
    devs = sys / "bus/pci/devices"
    devs.mkdir(parents=True)
    if vfio:
        (sys / "bus/pci/drivers/vfio-pci").mkdir(parents=True)
    dev = tmp_path / "dev/vfio"
    dev.mkdir(parents=True)
    for addr, (vendor, device, cls, driver, group) in devices.items():
        d = sys / "devices/pci" / addr
        d.mkdir(parents=True)
        (d / "vendor").write_text(f"0x{vendor:04x}\n")
        (d / "device").write_text(f"0x{device:04x}\n")
        (d / "class").write_text(f"0x{cls:06x}\n")
        if driver:
            drv = sys / "bus/pci/drivers" / driver
            drv.mkdir(parents=True, exist_ok=True)
            (d / "driver").symlink_to(drv)
        if group is not None:
            g = sys / "kernel/iommu_groups" / str(group)
            (g / "devices").mkdir(parents=True, exist_ok=True)
            (g / "devices" / addr).symlink_to(d)
            (d / "iommu_group").symlink_to(g)
            if driver == "vfio-pci":
                (dev / str(group)).touch()
                os.chmod(dev / str(group), 0o660 if node else 0o000)
        (devs / addr).symlink_to(d)
    return devs


def _dev(devs: Path) -> Path:
    return devs.parents[3] / "dev/vfio"


HOST = {
    GPU: (0x10DE, 0x2330, 0x030200, "nvidia", 14),
    GPU_AUDIO: (0x10DE, 0x22A3, 0x040300, "snd_hda_intel", 14),
    BRIDGE: (0x1022, 0x1483, 0x060400, "pcieport", 14),
    NVME: (0x144D, 0xA80A, 0x010802, "nvme", 3),
}
# The same host after the one-time setup moved the endpoints to vfio-pci.
BOUND = {
    a: (v, d, c, "pcieport" if a == BRIDGE else "vfio-pci", g)
    for a, (v, d, c, _, g) in HOST.items()
}


def test_options_list_gpus_first_and_hide_bridges(tmp_path):
    opts = pci.options(sysfs=_sysfs(tmp_path, HOST))
    assert [o["value"] for o in opts] == [GPU, NVME, GPU_AUDIO]
    assert opts[0]["label"] == (
        f"{GPU}  NVIDIA 3D controller [10de:2330]  IOMMU group 14  nvidia"
    )


def test_options_filter_on_the_label(tmp_path):
    opts = pci.options("samsung", sysfs=_sysfs(tmp_path, HOST))
    assert [o["value"] for o in opts] == [NVME]


def test_options_without_sysfs_is_empty(tmp_path):
    assert pci.options(sysfs=tmp_path / "absent") == []


def test_passthrough_of_a_whole_group_with_options(tmp_path):
    devs = _sysfs(tmp_path, BOUND)
    got = pci.passthrough(
        ["81:00.0", GPU_AUDIO], "rombar=0", sysfs=devs, dev=_dev(devs)
    )
    assert got == [
        {"addr": GPU, "opts": "rombar=0"},
        {"addr": GPU_AUDIO, "opts": "rombar=0"},
    ]


def test_passthrough_accepts_a_comma_string(tmp_path):
    devs = _sysfs(tmp_path, BOUND)
    assert pci.passthrough(f"{GPU}, {GPU_AUDIO}", sysfs=devs, dev=_dev(devs)) == [
        {"addr": GPU},
        {"addr": GPU_AUDIO},
    ]


def test_passthrough_of_nothing_is_empty(tmp_path):
    assert pci.passthrough(None, sysfs=tmp_path) == []
    assert pci.passthrough([], sysfs=tmp_path) == []


@pytest.mark.parametrize(
    "picked, needle",
    [
        ([GPU], "shares IOMMU group 14 with 0000:81:00.1"),
        (["0000:99:00.0"], "no PCI device"),
        (["not-an-address"], "is not a PCI address"),
        ([GPU, "81:00.0"], "listed twice"),
    ],
)
def test_passthrough_refuses(tmp_path, picked, needle):
    with pytest.raises(ValueError, match=needle):
        pci.passthrough(picked, sysfs=_sysfs(tmp_path, HOST))


def test_passthrough_refuses_a_device_outside_any_group(tmp_path):
    devs = _sysfs(tmp_path, {GPU: (0x10DE, 0x2330, 0x030200, "nvidia", None)})
    with pytest.raises(ValueError, match="no IOMMU group"):
        pci.passthrough([GPU], sysfs=devs)


def test_passthrough_refuses_a_device_left_on_its_host_driver(tmp_path):
    devs = _sysfs(tmp_path, {NVME: HOST[NVME]})
    with pytest.raises(ValueError) as e:
        pci.passthrough([NVME], sysfs=devs, dev=_dev(devs))
    assert "bound to nvme, not vfio-pci" in str(e.value)
    assert pci.REQUIREMENTS in str(e.value)


def test_passthrough_refuses_without_the_vfio_pci_driver(tmp_path):
    devs = _sysfs(tmp_path, {NVME: HOST[NVME]}, vfio=False)
    with pytest.raises(ValueError, match="vfio-pci driver is not loaded"):
        pci.passthrough([NVME], sysfs=devs, dev=_dev(devs))


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens any node")
def test_a_group_node_this_user_cannot_open_names_the_kvm_group(tmp_path):
    devs = _sysfs(tmp_path, {NVME: BOUND[NVME]}, node=False)
    with pytest.raises(ValueError) as e:
        pci.passthrough([NVME], sysfs=devs, dev=_dev(devs))
    assert "/dev/vfio/3" in str(e.value)
    assert "kvm group" in str(e.value)
    assert "user manager must have restarted" in str(e.value)
    assert pci.REQUIREMENTS in str(e.value)


def test_a_bound_device_passes(tmp_path):
    devs = _sysfs(tmp_path, BOUND)
    assert pci.passthrough([GPU, GPU_AUDIO], sysfs=devs, dev=_dev(devs)) == [
        {"addr": GPU},
        {"addr": GPU_AUDIO},
    ]


def test_the_access_checks_skip_with_check_access_off(tmp_path):
    devs = _sysfs(tmp_path, {NVME: HOST[NVME]}, vfio=False)
    got = pci.passthrough([NVME], sysfs=devs, check_access=False)
    assert got == [{"addr": NVME}]


@pytest.mark.parametrize(
    "opts", ["rombar=0 x-vga=on", "rombar=0\nx", "a\tb", "a\x01b", 'a="b"', "a\\b"]
)
def test_passthrough_refuses_opts_the_env_file_would_split(tmp_path, opts):
    with pytest.raises(ValueError, match="no whitespace, quotes or backslashes"):
        pci.passthrough(
            [NVME], opts, sysfs=_sysfs(tmp_path, BOUND), dev=tmp_path / "dev/vfio"
        )


def test_passthrough_strips_the_opts(tmp_path):
    devs = _sysfs(tmp_path, BOUND)
    got = pci.passthrough([NVME], "  rombar=0,x-vga=on\n", sysfs=devs, dev=_dev(devs))
    assert got == [{"addr": NVME, "opts": "rombar=0,x-vga=on"}]


@pytest.mark.parametrize(
    "picked, devices",
    [
        ([GPU], HOST),
        ([GPU], {GPU: (0x10DE, 0x2330, 0x030200, "nvidia", None)}),
    ],
)
def test_group_refusals_point_at_the_docs_section(tmp_path, picked, devices):
    with pytest.raises(ValueError, match=r"requirements\.rst \(PCI passthrough"):
        pci.passthrough(picked, sysfs=_sysfs(tmp_path, devices))


def test_passthrough_refuses_a_non_address(tmp_path):
    with pytest.raises(ValueError, match="not a PCI address string"):
        pci.passthrough([{"addr": NVME}], sysfs=_sysfs(tmp_path, HOST))


def test_memlock_needed_covers_ram_and_the_template_margin():
    assert pci.memlock_needed(8192) == (8192 + 256) * 1024 * 1024


# --- into the vars and the vendored templates --------------------------------


def _vars(monkeypatch, tmp_path, **fi):
    devs = _sysfs(tmp_path, BOUND)
    monkeypatch.setattr(pci, "SYSFS", devs)
    monkeypatch.setattr(pci, "DEV_VFIO", _dev(devs))
    return common.build_vars({"vm_name": "gpu0", **QEMU_BUILD, **fi})


def test_build_vars_omits_passthrough_when_none_is_picked(monkeypatch, tmp_path):
    v = _vars(monkeypatch, tmp_path)
    assert "pci_passthrough" not in v
    assert "service" not in v


def test_build_vars_adds_the_device_and_no_service_section(monkeypatch, tmp_path):
    v = _vars(monkeypatch, tmp_path, pci_passthrough=[NVME])
    assert v["pci_passthrough"] == [{"addr": NVME}]
    assert "service" not in v


def _render(monkeypatch, template, v):
    monkeypatch.setenv("VENDOR_DIR", str(REPO / "vendor"))
    return common.render(template, v)


def test_the_units_carry_each_passed_through_device(monkeypatch, tmp_path):
    v = _vars(
        monkeypatch,
        tmp_path,
        pci_passthrough=[GPU, GPU_AUDIO],
        pci_passthrough_opts="rombar=0",
        ram=8192,
    )
    env = _render(monkeypatch, "vm.env.j2", v)
    assert f"-device vfio-pci,host={GPU},rombar=0" in env
    assert f"-device vfio-pci,host={GPU_AUDIO},rombar=0" in env
    dropin = _render(monkeypatch, "qemu-system-override.conf.j2", v)
    assert "vfio-bind" not in dropin
    assert "LimitMEMLOCK=8448M" in dropin


def test_the_units_are_unchanged_without_passthrough(monkeypatch, tmp_path):
    dropin = _render(
        monkeypatch, "qemu-system-override.conf.j2", _vars(monkeypatch, tmp_path)
    )
    assert "vfio" not in dropin
    assert "LimitMEMLOCK" not in dropin


def test_the_vendored_udev_rules_cover_each_picked_device(monkeypatch, tmp_path):
    v = _vars(monkeypatch, tmp_path, pci_passthrough=[GPU, GPU_AUDIO])
    rules = _render(monkeypatch, "vfio-udev.rules.j2", v)
    assert 'SUBSYSTEM=="vfio", OWNER="root", GROUP="kvm", MODE="0660"' in rules
    for addr in (GPU, GPU_AUDIO):
        assert (
            f'ACTION=="add", SUBSYSTEM=="pci", KERNEL=="{addr}", '
            'ATTR{driver_override}="vfio-pci"'
        ) in rules
    assert "chmod" not in rules


LIMITS = """\
Limit                     Soft Limit           Hard Limit           Units
Max processes             254619               254619               processes
Max locked memory         8388608              {hard}              bytes
"""


def test_manager_memlock_reads_the_hard_limit():
    render = importlib.import_module("f.qsu.qemu-system.render")
    assert render.manager_memlock(LIMITS.format(hard="8388608")) == 8388608
    assert render.manager_memlock(LIMITS.format(hard="unlimited")) is None


def test_the_user_manager_is_found_through_its_cgroup(monkeypatch, tmp_path):
    render = importlib.import_module("f.qsu.qemu-system.render")
    files = {
        "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/init.scope/"
        "cgroup.procs": "41\n42\n",
        "/proc/41/comm": "(sd-pam)\n",
        "/proc/42/comm": "systemd\n",
        "/proc/42/limits": LIMITS.format(hard="unlimited"),
    }

    def read_text(self, *_, **__):
        return files[str(self)]

    monkeypatch.setattr(Path, "read_text", read_text)
    assert render.manager_memlock(render._user_manager_limits(1000)) is None


RENDER = importlib.import_module("f.qsu.qemu-system.render")
MIB = 1024 * 1024


@pytest.mark.parametrize(
    "hard, ok",
    [
        (str((4096 + 256) * MIB), True),
        (str((4096 + 256) * MIB - 1), False),
        ("unlimited", True),
    ],
)
def test_check_memlock_needs_ram_plus_the_margin(monkeypatch, hard, ok):
    monkeypatch.setattr(
        RENDER, "_user_manager_limits", lambda uid: LIMITS.format(hard=hard)
    )
    if ok:
        RENDER._check_memlock(4096)
        return
    with pytest.raises(ValueError) as e:
        RENDER._check_memlock(4096)
    msg = str(e.value)
    assert "needs 4352 MiB of locked memory (4096 MiB of guest RAM plus 256" in msg
    assert "LimitMEMLOCK is 4351 MiB" in msg
    assert pci.REQUIREMENTS in msg


def test_a_missing_user_manager_cgroup_is_refused(monkeypatch):
    def read_text(self, *_, **__):
        raise FileNotFoundError(2, "No such file or directory", str(self))

    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(ValueError) as e:
        RENDER._user_manager_limits(1000)
    assert "user@1000.service/init.scope/cgroup.procs" in str(e.value)
    assert pci.REQUIREMENTS in str(e.value)


def _render_main(monkeypatch, tmp_path, **kw):
    devs = _sysfs(tmp_path, BOUND)
    monkeypatch.setattr(pci, "SYSFS", devs)
    monkeypatch.setattr(pci, "DEV_VFIO", _dev(devs))
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("WM_ROOT_FLOW_JOB_ID", raising=False)
    return RENDER.main(
        vm_name="gpu0",
        auto_vm_name=False,
        kernel_image="/boot/bzImage",
        modules_dir="/lib/modules/x",
        nvme_drive_count=0,
        **QEMU_BUILD,
        **kw,
    )


@pytest.mark.parametrize(
    "picked, needle",
    [([GPU], "shares IOMMU group"), ([NVME], "LimitMEMLOCK is 8 MiB")],
)
def test_render_refuses_before_writing_any_unit(monkeypatch, tmp_path, picked, needle):
    monkeypatch.setattr(
        RENDER, "_user_manager_limits", lambda uid: LIMITS.format(hard="8388608")
    )
    with pytest.raises(ValueError, match=needle):
        _render_main(monkeypatch, tmp_path, pci_passthrough=picked)
    assert not (tmp_path / "config").exists()
    assert not (tmp_path / "workers/shared").exists()
