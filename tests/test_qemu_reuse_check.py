# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the QEMU install-reuse probe (`f.qemu.reuse_check`)."""

import os

from f.qemu import reuse_check

ENV = ("STORE_INDEX_DIR", "SYSTEM_DIR", "WORKBENCH_DIR", "WORKERS_DIR", "MIRRORS_DIR")


def _clear_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_absent_everywhere_reports_not_present(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    prefix = tmp_path / "11.0.0-abc123def456"
    assert reuse_check.main(str(prefix)) == {
        "present": False,
        "prefix": str(prefix),
        "qemu_binary": None,
    }


def test_local_prefix_resolves_the_first_sorted_emulator(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    prefix = tmp_path / "11.0.0-abc123def456"
    (prefix / "bin").mkdir(parents=True)
    for name in ("qemu-system-x86_64", "qemu-system-aarch64", "qemu-img"):
        (prefix / "bin" / name).write_text("")
    out = reuse_check.main(str(prefix))
    assert out["present"] is True
    assert out["qemu_binary"] == str(prefix / "bin" / "qemu-system-aarch64")


def test_store_entry_backs_an_empty_prefix(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    index = tmp_path / "store-index"
    index.mkdir()
    monkeypatch.setenv("STORE_INDEX_DIR", str(index))
    tree = tmp_path / "tree"
    (tree / "bin").mkdir(parents=True)
    (tree / "bin" / "qemu-system-x86_64").write_text("")
    prefix = tmp_path / "destdir" / "11.0.0-abc123def456"
    (index / "qemu-11.0.0-abc123def456").symlink_to(tree)
    out = reuse_check.main(str(prefix))
    real = os.path.realpath(tree)
    assert out["present"] is True
    assert out["qemu_binary"] == os.path.join(real, "bin", "qemu-system-x86_64")
