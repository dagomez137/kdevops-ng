# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the qsu vars/render helpers (`f.qsu.common`)."""

import os
from pathlib import Path

import jinja2
import pytest
import yaml

from f.qsu import common

ENV = (
    "STORE_INDEX_DIR",
    "SYSTEM_DIR",
    "WORKBENCH_DIR",
    "WORKERS_DIR",
    "MIRRORS_DIR",
    "VENDOR_DIR",
    "WM_ROOT_FLOW_JOB_ID",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
)

QEMU_BUILD = {
    "qemu_source": "qemu-build",
    "qemu_binary": "/builds/qemu/bin/qemu-system-x86_64",
    "custom_virtiofsd": True,
    "virtiofsd_binary": "/builds/virtiofsd/bin/virtiofsd",
}


def _clear_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def _fi(**over):
    return {"vm_name": "demo", **QEMU_BUILD, **over}


def test_main_names_the_library():
    assert "f/qsu/common" in common.main()


def test_qsu_dir_honors_vendor_dir(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("VENDOR_DIR", "/v")
    assert common.qsu_dir() == Path("/v/qemu-system-units")


def test_qsu_dir_derives_vendor_from_the_workers_root(monkeypatch):
    _clear_env(monkeypatch)
    assert common.qsu_dir(Path("/wb/workers")) == Path("/vendor/qemu-system-units")


def test_resolve_vm_name_auto_slugs_the_flow_job_id(monkeypatch):
    monkeypatch.setenv("WM_ROOT_FLOW_JOB_ID", "0195c2aa-dead-beef")
    fi = {"auto_vm_name": True, "vm_name": "manual"}
    assert common.resolve_vm_name(fi) == "vm-0195c2aa"


def test_resolve_vm_name_auto_without_a_job_id_uses_the_given_name(monkeypatch):
    monkeypatch.delenv("WM_ROOT_FLOW_JOB_ID", raising=False)
    assert common.resolve_vm_name({"auto_vm_name": True, "vm_name": "manual"}) == (
        "manual"
    )


def test_resolve_vm_name_off_ignores_the_job_id(monkeypatch):
    monkeypatch.setenv("WM_ROOT_FLOW_JOB_ID", "0195c2aa-dead-beef")
    assert common.resolve_vm_name({"auto_vm_name": False, "vm_name": "manual"}) == (
        "manual"
    )


def test_render_matches_minijinja_trim_blocks(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("VENDOR_DIR", str(tmp_path / "vendor"))
    templates = tmp_path / "vendor/qemu-system-units/templates"
    templates.mkdir(parents=True)
    (templates / "t.j2").write_text("{% if on %}\nyes\n{% endif %}\nname={{ name }}\n")
    assert common.render("t.j2", {"on": True, "name": "demo"}) == "yes\nname=demo\n"


def test_render_raises_on_an_undefined_var(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("VENDOR_DIR", str(tmp_path / "vendor"))
    templates = tmp_path / "vendor/qemu-system-units/templates"
    templates.mkdir(parents=True)
    (templates / "t.j2").write_text("{{ missing }}\n")
    with pytest.raises(jinja2.UndefinedError):
        common.render("t.j2", {})


def test_write_unit_writes_and_creates_parents(tmp_path, capsys):
    path = tmp_path / "user/qemu-system@.service"
    common.write_unit(path, "[Unit]\n")
    assert path.read_text() == "[Unit]\n"
    assert "wrote" in capsys.readouterr().out


def test_write_unit_skips_an_identical_rewrite(tmp_path, capsys):
    path = tmp_path / "unit"
    path.write_text("[Unit]\n")
    os.utime(path, (1, 1))
    common.write_unit(path, "[Unit]\n")
    assert path.stat().st_mtime == 1
    assert "unchanged" in capsys.readouterr().out


def test_write_unit_rewrites_on_a_content_change(tmp_path):
    path = tmp_path / "unit"
    path.write_text("[Unit]\n")
    common.write_unit(path, "[Unit]\nX=1\n")
    assert path.read_text() == "[Unit]\nX=1\n"


def test_systemd_config_prefers_the_home_argument(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    assert common.systemd_config(Path("/custom")) == Path("/custom/systemd")


def test_systemd_config_reads_xdg_config_home(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    assert common.systemd_config() == Path("/xdg/systemd")


def test_systemd_config_falls_back_to_dot_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert common.systemd_config() == tmp_path / ".config/systemd"


def test_state_dir_reads_xdg_state_home(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/state")
    assert common.state_dir("demo") == Path("/state/qemu-system/demo")


def test_state_dir_falls_back_to_local_state(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert common.state_dir("demo") == tmp_path / ".local/state/qemu-system/demo"


def test_running_vms_parses_listed_units():
    out = (
        "qemu-system@vm-a.service loaded active running QEMU vm-a\n"
        "* qemu-system@vm-b.service loaded failed failed QEMU vm-b\n"
        "other@x.service loaded active running other\n"
        "\n"
    )
    assert common._running_vms(out) == {"vm-a", "vm-b"}


def test_running_vms_empty_output_is_empty():
    assert common._running_vms("") == set()


def test_peer_hosts_read_the_registry(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    assert common._peer_hosts() == []
    (tmp_path / "peers").write_text("hostb\t/idx\nhostc\n")
    assert common._peer_hosts() == ["hostb", "hostc"]


def test_shares_default_is_the_store_share(monkeypatch):
    _clear_env(monkeypatch)
    shares = common._shares({}, None)
    assert shares == [{"tag": "store", "dir": "/nix/store", "mount": "/nix/store"}]


def test_shares_wire_modules_to_lib_modules(monkeypatch):
    _clear_env(monkeypatch)
    shares = common._shares({}, "/store/kernel/lib/modules")
    assert {
        "tag": "modules",
        "dir": "/store/kernel/lib/modules",
        "mount": "/lib/modules",
    } in shares


def test_shares_explicit_list_replaces_everything(monkeypatch):
    _clear_env(monkeypatch)
    mine = [{"tag": "x", "dir": "/x", "mount": "/x"}]
    assert common._shares({"shares": mine}, "/m") == mine


@pytest.mark.parametrize(
    ("knob", "tag", "mount"),
    [
        ("fstests", "fstests", "/var/lib/xfstests"),
        ("selftests", "selftests", "/var/lib/kselftests"),
        ("usertests", "usertests", "/var/lib/usertests"),
    ],
)
def test_shares_suite_dirs_are_per_vm_under_workers(
    monkeypatch, tmp_path, knob, tag, mount
):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    shares = common._shares({knob: True, "vm_name": "demo"}, None)
    expect = str((tmp_path / "shared" / tag / "demo").resolve())
    assert {"tag": tag, "dir": expect, "mount": mount} in shares


@pytest.mark.parametrize("knob", ["fstests", "selftests", "usertests"])
def test_shares_suite_without_a_vm_name_raises(monkeypatch, tmp_path, knob):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="valid vm_name"):
        common._shares({knob: True}, None)


def test_shares_suite_rejects_a_traversing_vm_name(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="valid vm_name"):
        common._shares({"fstests": True, "vm_name": "../evil"}, None)


def test_shares_home_share_is_read_only_by_default(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    shares = common._shares({"home_share": True}, None)
    home = str(tmp_path)
    assert {"tag": "home", "dir": home, "mount": home, "options": ["ro"]} in shares


def test_shares_home_share_readwrite_drops_ro(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    shares = common._shares({"home_share": True, "home_share_readwrite": True}, None)
    home = str(tmp_path)
    assert {"tag": "home", "dir": home, "mount": home} in shares


def test_shares_controller_share_defaults_mirror_the_dir(monkeypatch):
    _clear_env(monkeypatch)
    fi = {"controller_share": True, "controller_share_dir": "/data"}
    shares = common._shares(fi, None)
    assert {
        "tag": "controller-share",
        "dir": "/data",
        "mount": "/data",
        "options": ["ro"],
    } in shares


def test_shares_controller_share_custom_tag_and_mount(monkeypatch):
    _clear_env(monkeypatch)
    fi = {
        "controller_share": True,
        "controller_share_tag": "results",
        "controller_share_dir": "/data",
        "controller_share_guest_mount": "/mnt/data",
        "controller_share_readwrite": True,
    }
    shares = common._shares(fi, None)
    assert {"tag": "results", "dir": "/data", "mount": "/mnt/data"} in shares


def test_predefined_share_tags_stay_within_the_canonical_set(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    fi = {
        "vm_name": "demo",
        "fstests": True,
        "selftests": True,
        "usertests": True,
        "home_share": True,
        "controller_share": True,
    }
    tags = {s["tag"] for s in common._shares(fi, "/m")}
    assert tags <= set(common.CANONICAL_SHARE_TAGS)


def test_drive_pick_bare_value_applies_to_every_drive():
    assert common._drive_pick("4096", 0) == "4096"
    assert common._drive_pick("4096", 3) == "4096"


def test_drive_pick_comma_list_assigns_by_index():
    assert common._drive_pick(" 4096 , 512 ", 0) == "4096"
    assert common._drive_pick(" 4096 , 512 ", 1) == "512"
    assert common._drive_pick("4096,512", 2) == ""


def test_drive_pick_none_is_empty():
    assert common._drive_pick(None, 0) == ""


def test_nvme_drives_explicit_list_wins():
    mine = [{"file": "x.qcow2"}]
    assert common.nvme_drives({"nvme_drives": mine}) == mine


def test_nvme_drives_zero_count_is_empty():
    assert common.nvme_drives({"nvme_drive_count": 0}) == []
    assert common.nvme_drives({}) == []


def test_nvme_drives_simple_mode_names_and_blockconf():
    fi = {"nvme_drive_count": 2, "logical_block_size": "4096,512"}
    drives = common.nvme_drives(fi)
    assert drives == [
        {
            "file": "nvme0.qcow2",
            "format": "qcow2",
            "serial": "kdevops0",
            "logical_block_size": "4096",
        },
        {
            "file": "nvme1.qcow2",
            "format": "qcow2",
            "serial": "kdevops1",
            "logical_block_size": "512",
        },
    ]


def test_nvme_drives_write_cache_uses_the_dashed_key():
    drives = common.nvme_drives({"nvme_drive_count": 1, "write_cache": "on"})
    assert drives[0]["write-cache"] == "on"


def test_nvme_drives_atomic_dn_marks_every_controller():
    drives = common.nvme_drives({"nvme_drive_count": 2, "atomic_dn": True})
    assert all(d["atomic.dn"] is True for d in drives)


def test_nvme_drives_ns_knobs_force_explicit_namespaces():
    fi = {"nvme_drive_count": 1, "atomic_nawun": "3", "logical_block_size": "4096"}
    drives = common.nvme_drives(fi)
    assert drives == [
        {
            "serial": "kdevops0",
            "namespaces": [
                {
                    "file": "nvme0.qcow2",
                    "format": "qcow2",
                    "logical_block_size": "4096",
                    "atomic.nawun": "3",
                }
            ],
        }
    ]


def test_nvme_drives_atomic_mam_marks_every_namespace():
    fi = {
        "nvme_drive_count": 2,
        "atomic_nawun": "15",
        "atomic_nawupf": "15",
        "atomic_nabsn": "15",
        "atomic_nabspf": "15",
        "atomic_mam": True,
    }
    drives = common.nvme_drives(fi)
    assert all(d["namespaces"][0]["atomic.mam"] is True for d in drives)


def test_nvme_drives_atomic_mam_off_is_not_set():
    fi = {"nvme_drive_count": 1, "atomic_nawun": "15", "atomic_mam": False}
    ns = common.nvme_drives(fi)[0]["namespaces"][0]
    assert "atomic.mam" not in ns


def test_nvme_drives_cmb_applies_per_drive():
    fi = {"nvme_drive_count": 2, "cmb_size_mb": "64,0", "legacy_cmb": True}
    drives = common.nvme_drives(fi)
    assert drives[0]["cmb_size_mb"] == "64"
    assert drives[0]["legacy-cmb"] is True
    assert "cmb_size_mb" not in drives[1]
    assert "legacy-cmb" not in drives[1]


def test_nvme_drives_pmr_valid_size_lands_on_the_controller():
    page = os.sysconf("SC_PAGESIZE")
    drives = common.nvme_drives({"nvme_drive_count": 1, "pmr_size": str(page)})
    assert drives[0]["pmr"] == {"size": page}


def test_nvme_drives_pmr_share_off_is_recorded():
    page = os.sysconf("SC_PAGESIZE")
    fi = {"nvme_drive_count": 1, "pmr_size": str(page), "pmr_share": False}
    assert common.nvme_drives(fi)[0]["pmr"] == {"size": page, "share": False}


def test_nvme_drives_pmr_pmem_requires_share():
    page = os.sysconf("SC_PAGESIZE")
    fi = {
        "nvme_drive_count": 1,
        "pmr_size": str(page),
        "pmr_share": False,
        "pmr_pmem": True,
    }
    with pytest.raises(ValueError, match="pmr_pmem requires pmr_share"):
        common.nvme_drives(fi)


def test_nvme_drives_pmr_pmem_with_share_is_recorded():
    page = os.sysconf("SC_PAGESIZE")
    fi = {"nvme_drive_count": 1, "pmr_size": str(page), "pmr_pmem": True}
    assert common.nvme_drives(fi)[0]["pmr"] == {"size": page, "pmem": True}


def test_nvme_drives_pmr_rejects_a_non_integer():
    with pytest.raises(ValueError, match="not an integer"):
        common.nvme_drives({"nvme_drive_count": 1, "pmr_size": "big"})


def test_nvme_drives_pmr_rejects_a_sub_page_or_non_pow2_size():
    with pytest.raises(ValueError, match="power of 2"):
        common.nvme_drives({"nvme_drive_count": 1, "pmr_size": "24"})
    with pytest.raises(ValueError, match="power of 2"):
        common.nvme_drives({"nvme_drive_count": 1, "pmr_size": "16"})


def test_nvme_drives_pmr_zero_means_no_pmr():
    drives = common.nvme_drives({"nvme_drive_count": 1, "pmr_size": "0"})
    assert "pmr" not in drives[0]


def test_kernel_none_without_an_image():
    assert common._kernel({}, None, None) is None
    assert common._kernel({}, {"modules": "/m"}, None) is None


def _kernel(fi: dict, kernel: dict | None, closure: dict | None) -> dict:
    k = common._kernel(fi, kernel, closure)
    assert k is not None
    return k


def test_kernel_explicit_image_wins_over_the_manifest():
    k = _kernel({"kernel_image": "/boot/mine"}, {"bzImage": "/boot/built"}, None)
    assert k["image"] == "/boot/mine"


def test_kernel_manifest_prefers_vmlinuz_over_bzimage():
    manifest = {"vmlinuz": "/boot/vmlinuz", "bzImage": "/boot/bzImage"}
    assert _kernel({}, manifest, None)["image"] == "/boot/vmlinuz"


def test_kernel_append_composes_from_the_closure_init():
    k = _kernel({}, {"bzImage": "/b"}, {"init": "/nix/store/i-init"})
    assert k["append"] == (
        "root=tmpfs console=ttyS0,115200 console=hvc0 init=/nix/store/i-init"
    )


def test_kernel_explicit_append_wins():
    fi = {"kernel_append": "console=ttyS0"}
    k = _kernel(fi, {"bzImage": "/b"}, {"init": "/i"})
    assert k["append"] == "console=ttyS0"


def test_kernel_parameters_ride_after_the_append():
    fi = {"kernel_append": "root=tmpfs", "kernel_parameters": ["kunit.autorun=1"]}
    k = _kernel(fi, {"bzImage": "/b"}, None)
    assert k["append"] == "root=tmpfs kunit.autorun=1"


def test_kernel_parameters_stand_alone_without_an_append():
    fi = {"kernel_parameters": ["quiet", "kunit.autorun=1"]}
    k = _kernel(fi, {"bzImage": "/b"}, None)
    assert k["append"] == "quiet kunit.autorun=1"


def test_kernel_no_append_key_when_nothing_composes():
    assert common._kernel({}, {"bzImage": "/b"}, None) == {"image": "/b"}


def test_kernel_initrd_priority_fi_then_closure_then_manifest():
    fi = {"kernel_initrd": "/fi-ird"}
    closure = {"initrd": "/cl-ird"}
    manifest = {"bzImage": "/b", "initrd": "/kn-ird"}
    assert _kernel(fi, manifest, closure)["initrd"] == "/fi-ird"
    assert _kernel({}, manifest, closure)["initrd"] == "/cl-ird"
    assert _kernel({}, manifest, None)["initrd"] == "/kn-ird"


def test_port_offset_explicit_vm_index_wins():
    assert common._port_offset({"vm_index": 7, "vm_name": "demo"}) == 7


def test_port_offset_hashes_the_vm_name_stably():
    assert common._port_offset({"vm_name": "demo"}) == 4375
    assert common._port_offset({"vm_name": "vm-abc"}) == 3082


def test_port_offset_stays_within_the_modulo():
    off = common._port_offset({"vm_name": "any-name"})
    assert 0 <= off < common.PORT_OFFSET_MODULO


def test_build_vars_defaults_and_ports(monkeypatch):
    _clear_env(monkeypatch)
    v = common.build_vars(_fi(vm_index=2))
    assert v["vm_name"] == "demo"
    assert v["service_scope"] == "user"
    assert v["qemu_binary"] == QEMU_BUILD["qemu_binary"]
    assert v["virtiofsd_binary"] == QEMU_BUILD["virtiofsd_binary"]
    assert (v["cpu"], v["accel"], v["machine_type"]) == ("host", "kvm", "q35")
    assert (v["ram"], v["cpus"]) == (4096, 4)
    assert v["share_transport"] == "virtiofs"
    assert v["ssh_port"] == 10022 + 2
    assert v["vsock_cid"] == 100 + 2
    assert "nvme" not in v
    assert "kernel" not in v
    assert "iommu" not in v


def test_build_vars_hashed_offset_feeds_both_ports(monkeypatch):
    _clear_env(monkeypatch)
    v = common.build_vars(_fi())
    assert v["ssh_port"] == 10022 + 4375
    assert v["vsock_cid"] == 100 + 4375


def test_build_vars_explicit_port_and_cid_win(monkeypatch):
    _clear_env(monkeypatch)
    v = common.build_vars(_fi(ssh_port="2222", vsock_cid="77", vm_index=3))
    assert (v["ssh_port"], v["vsock_cid"]) == (2222, 77)


def test_build_vars_takes_modules_from_the_kernel_manifest(monkeypatch):
    _clear_env(monkeypatch)
    kernel = {"bzImage": "/boot/bz", "modules": "/store/lib/modules"}
    v = common.build_vars(_fi(), kernel=kernel)
    assert {
        "tag": "modules",
        "dir": "/store/lib/modules",
        "mount": "/lib/modules",
    } in v["shares"]
    assert v["kernel"]["image"] == "/boot/bz"


def test_build_vars_explicit_kernel_takes_the_explicit_modules(monkeypatch):
    _clear_env(monkeypatch)
    kernel = {"bzImage": "/boot/bz", "modules": "/manifest/modules"}
    fi = _fi(kernel_image="/boot/mine", modules_dir="/mine/modules")
    v = common.build_vars(fi, kernel=kernel)
    tags = {s["tag"]: s for s in v["shares"]}
    assert tags["modules"]["dir"] == "/mine/modules"
    assert v["kernel"]["image"] == "/boot/mine"


def test_build_vars_nvme_and_iommu_pass_through(monkeypatch):
    _clear_env(monkeypatch)
    v = common.build_vars(_fi(nvme_drive_count=1, iommu="intel-iommu"))
    assert v["nvme"]["drives"][0]["file"] == "nvme0.qcow2"
    assert v["iommu"] == "intel-iommu"


def test_emit_vars_yaml_snapshots_sorted_vars(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("VENDOR_DIR", str(tmp_path / "vendor"))
    out = common.emit_vars_yaml("demo", {"b": 2, "a": 1})
    dest = Path(out)
    assert dest == tmp_path / "vendor/qemu-system-units/vars/demo.yaml"
    assert yaml.safe_load(dest.read_text()) == {"a": 1, "b": 2}
