# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the bringup reuse resolution (`f.qsu.resolve`)."""

import json
import os
from pathlib import Path

import pytest

from f.qsu import resolve

ENV = ("STORE_INDEX_DIR", "SYSTEM_DIR", "WORKBENCH_DIR", "WORKERS_DIR")
RELEASE = "6.9.0-test"


def _clear_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def _index(monkeypatch, tmp_path):
    index = tmp_path / "store-index"
    index.mkdir()
    monkeypatch.setenv("STORE_INDEX_DIR", str(index))
    return index


def _sidecar(monkeypatch, tmp_path, vm_name, data):
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    vm = tmp_path / "shared/vm"
    vm.mkdir(parents=True, exist_ok=True)
    (vm / f"{vm_name}.vars.json").write_text(json.dumps(data))


def test_host_operator_reads_the_home_owner(monkeypatch):
    monkeypatch.setenv("HOME", "/home/alice/")
    assert resolve.host_operator() == "alice"


def test_host_operator_never_returns_root(monkeypatch):
    monkeypatch.setenv("HOME", "/home/root")
    assert resolve.host_operator() == "kdevops"


def test_host_operator_falls_back_to_the_login_name(monkeypatch):
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.setenv("LOGNAME", "bob")
    assert resolve.host_operator() == "bob"


def test_kernel_reuse_without_a_pick_raises():
    with pytest.raises(ValueError, match="no kernel was picked"):
        resolve.main(kernel_reuse=True)


def test_qemu_reuse_with_an_empty_index_raises(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    _index(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="no QEMU is in the store index"):
        resolve.main(qemu_reuse=True)


def test_kernel_pick_resolves_the_run_layer(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    index = _index(monkeypatch, tmp_path)
    root = tmp_path / "kernel-tree"
    (root / "boot").mkdir(parents=True)
    (root / "boot" / f"vmlinuz-{RELEASE}").write_text("")
    (root / "lib/modules" / RELEASE).mkdir(parents=True)
    (index / f"kernel-{RELEASE}").symlink_to(root)
    out = resolve.main(kernel_index=f"kernel-{RELEASE}")
    real = Path(os.path.realpath(root))
    assert out["kernel"] == {
        "bzImage": str(real / "boot" / f"vmlinuz-{RELEASE}"),
        "modules": str(real / "lib/modules"),
        "uts_release": RELEASE,
    }
    assert out["qemu_binary"] is None
    assert out["closure"] == {}
    assert out["sharing"] == {}


def test_kernel_pick_that_no_longer_resolves_raises(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    index = _index(monkeypatch, tmp_path)
    (index / "kernel-gone").symlink_to(tmp_path / "collected")
    with pytest.raises(ValueError, match="no store-index entry"):
        resolve.main(kernel_index="kernel-gone")


def test_kernel_pick_without_a_run_layer_raises(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    index = _index(monkeypatch, tmp_path)
    root = tmp_path / "kernel-tree"
    (root / "boot").mkdir(parents=True)
    (root / "boot" / f"vmlinuz-{RELEASE}").write_text("")
    (index / f"kernel-{RELEASE}").symlink_to(root)
    with pytest.raises(ValueError, match="no image/modules"):
        resolve.main(kernel_index=f"kernel-{RELEASE}")


def test_qemu_pick_resolves_the_first_emulator(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    index = _index(monkeypatch, tmp_path)
    root = tmp_path / "qemu-tree"
    (root / "bin").mkdir(parents=True)
    for name in ("qemu-system-x86_64", "qemu-system-aarch64", "qemu-img"):
        (root / "bin" / name).write_text("")
    (index / "qemu-abc123").symlink_to(root)
    out = resolve.main(qemu_index="qemu-abc123")
    assert out["qemu_binary"].endswith("bin/qemu-system-aarch64")
    assert out["kernel"] == {}


def test_qemu_pick_without_an_emulator_raises(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    index = _index(monkeypatch, tmp_path)
    root = tmp_path / "qemu-tree"
    root.mkdir()
    (index / "qemu-abc123").symlink_to(root)
    with pytest.raises(ValueError, match="qemu-system binary"):
        resolve.main(qemu_index="qemu-abc123")


def test_qemu_reuse_auto_picks_the_latest_index(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    index = _index(monkeypatch, tmp_path)
    root = tmp_path / "qemu-tree"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "qemu-system-x86_64").write_text("")
    (index / "qemu-abc123").symlink_to(root)
    out = resolve.main(qemu_reuse=True)
    assert out["qemu_binary"].endswith("bin/qemu-system-x86_64")


def test_closure_reuse_without_a_sidecar_raises(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="render sidecar"):
        resolve.main(closure_reuse=True, vm_name="demo")


def test_closure_reuse_replays_the_sidecar(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HOME", "/home/alice")
    _sidecar(
        monkeypatch,
        tmp_path,
        "demo",
        {
            "closure": {"init": "/nix/store/a-init", "initrd": "/nix/store/b-ird"},
            "sharing": {"virtiofs": True},
        },
    )
    out = resolve.main(closure_reuse=True, vm_name="demo")
    assert out["closure"] == {
        "init": "/nix/store/a-init",
        "initrd": "/nix/store/b-ird",
    }
    assert out["sharing"] == {"virtiofs": True}
    assert out["kernel"] == {}
    assert out["qemu_binary"] is None
    assert out["host_user"] == "alice"


def test_sidecar_without_an_init_raises(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    _sidecar(monkeypatch, tmp_path, "demo", {"closure": {}, "sharing": {}})
    with pytest.raises(ValueError, match="no closure init/initrd"):
        resolve.main(closure_reuse=True, vm_name="demo")
