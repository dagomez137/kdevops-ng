# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the build-area directory resolution (`f.common.devshell`)."""

import pytest

from f.common import devshell

ENV = (
    "WORKBENCH_DIR",
    "WORKERS_DIR",
    "WORKTREES_DIR",
    "SYSTEM_DIR",
    "MIRRORS_DIR",
    "CCACHE_DIR",
    "STORE_INDEX_DIR",
    "VENDOR_DIR",
)


def _clear_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_explicit_env_wins_for_every_directory(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    for name, resolve in (
        ("WORKBENCH_DIR", devshell.workbench_dir),
        ("WORKTREES_DIR", devshell.worktrees_dir),
        ("SYSTEM_DIR", devshell.system_dir),
        ("MIRRORS_DIR", devshell.mirrors_dir),
        ("CCACHE_DIR", devshell.ccache_dir),
        ("STORE_INDEX_DIR", devshell.store_index_dir),
        ("VENDOR_DIR", devshell.vendor_dir),
    ):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
        assert resolve() == tmp_path / name.lower()
        monkeypatch.delenv(name)


def test_fallback_chain_hangs_off_the_workers_parent(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    assert devshell.workbench_dir() == tmp_path
    assert devshell.worktrees_dir() == tmp_path
    assert devshell.system_dir() == tmp_path / "system"
    assert devshell.mirrors_dir() == tmp_path / "system" / "mirror"
    assert devshell.ccache_dir() == tmp_path / "system" / "ccache"
    assert devshell.store_index_dir() == tmp_path / "system" / "store-index"
    assert devshell.vendor_dir() == tmp_path.parent / "vendor"


def test_workbench_env_overrides_the_workers_parent(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    monkeypatch.setenv("WORKBENCH_DIR", str(tmp_path / "bench"))
    assert devshell.workbench_dir() == tmp_path / "bench"
    assert devshell.worktrees_dir() == tmp_path / "bench"
    assert devshell.system_dir() == tmp_path / "bench" / "system"


def test_system_env_relocates_its_children(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path / "sys"))
    assert devshell.mirrors_dir() == tmp_path / "sys" / "mirror"
    assert devshell.ccache_dir() == tmp_path / "sys" / "ccache"
    assert devshell.store_index_dir() == tmp_path / "sys" / "store-index"


def test_vendor_dir_derives_from_a_given_workers_root(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    workers = tmp_path / "bench" / "workers"
    assert devshell.vendor_dir(workers) == tmp_path / "vendor"
    assert devshell.vendor_dir(str(workers)) == tmp_path / "vendor"


def test_nothing_resolvable_raises_keyerror(monkeypatch):
    _clear_env(monkeypatch)
    for resolve in (
        devshell.workbench_dir,
        devshell.worktrees_dir,
        devshell.system_dir,
        devshell.mirrors_dir,
        devshell.ccache_dir,
        devshell.store_index_dir,
        devshell.vendor_dir,
    ):
        with pytest.raises(KeyError):
            resolve()
