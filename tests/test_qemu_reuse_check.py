# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the QEMU install-reuse probe (`f.qemu.reuse_check`)."""

import os

import pytest

from f.qemu import fetch_devel, reuse_check

ENV = ("STORE_INDEX_DIR", "SYSTEM_DIR", "WORKBENCH_DIR", "WORKERS_DIR", "MIRRORS_DIR")


def _clear_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_absent_everywhere_reports_not_present(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    prefix = tmp_path / "11.0.0-abc123def456"
    assert reuse_check.main(str(prefix)) == {
        "present": False,
        "devel_present": False,
        "prefix": str(prefix),
        "qemu_binary": None,
    }


def test_the_two_layers_are_reported_independently(monkeypatch, tmp_path):
    """A published run layer says nothing about the devel layer, and the reverse."""
    _clear_env(monkeypatch)
    index = tmp_path / "store-index"
    index.mkdir()
    monkeypatch.setenv("STORE_INDEX_DIR", str(index))
    tree = tmp_path / "tree"
    (tree / "bin").mkdir(parents=True)
    (tree / "bin" / "qemu-system-x86_64").write_text("")
    prefix = tmp_path / "destdir" / "11.0.0-abc123def456"

    # The exact shape that silently produced an unindexed worktree: run layer
    # published, devel layer never was.
    (index / "qemu-11.0.0-abc123def456").symlink_to(tree)
    out = reuse_check.main(str(prefix))
    assert out["present"] is True and out["devel_present"] is False

    devel = tmp_path / "devel"
    devel.mkdir()
    (index / "qemu-devel-11.0.0-abc123def456").symlink_to(devel)
    out = reuse_check.main(str(prefix))
    assert out["present"] is True and out["devel_present"] is True

    # A devel layer alone is not a run-layer hit.
    (index / "qemu-11.0.0-abc123def456").unlink()
    out = reuse_check.main(str(prefix))
    assert out["present"] is False and out["devel_present"] is True


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


def test_a_required_fetch_fails_instead_of_leaving_a_bare_checkout(
    monkeypatch, tmp_path
):
    _clear_env(monkeypatch)
    index = tmp_path / "store-index"
    index.mkdir()
    monkeypatch.setenv("STORE_INDEX_DIR", str(index))
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    worktree = tmp_path / "vanilla/qemu"
    worktree.mkdir(parents=True)
    (worktree / "VERSION").write_text("11.0.0\n")

    args = dict(worktree=str(worktree), prefix="11.0.0-abc123def456")
    assert fetch_devel.main(**args)["fetched"] is False
    with pytest.raises(FileNotFoundError, match="qemu-devel-11.0.0-abc123def456"):
        fetch_devel.main(**args, required=True)
