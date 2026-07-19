# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the peer-fetch step's no-network paths (`f.qemu.fetch_identity`)."""

from f.qemu import fetch_identity

ENV = ("STORE_INDEX_DIR", "SYSTEM_DIR", "WORKBENCH_DIR", "WORKERS_DIR", "MIRRORS_DIR")
PREFIX = "/dest/11.0.0-abc123def456"


def _clear_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_peer_fetch_off_builds_locally():
    assert fetch_identity.main(PREFIX, use_peers=False) == {
        "fetched": False,
        "prefix": PREFIX,
    }


def test_no_registered_peers_builds_locally(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path / "system"))
    assert fetch_identity.main(PREFIX) == {"fetched": False, "prefix": PREFIX}
