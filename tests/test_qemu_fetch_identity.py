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


def test_devel_is_only_swept_when_asked_for(monkeypatch, tmp_path):
    """The devel layer is not part of the run-layer sweep, so it reports separately."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path / "system"))
    assert "devel_fetched" not in fetch_identity.main(PREFIX)
    assert fetch_identity.main(PREFIX, devel=True) == {
        "fetched": False,
        "prefix": PREFIX,
        "devel_fetched": False,
    }


def test_a_local_devel_layer_is_not_swept_for(monkeypatch, tmp_path):
    """Already having it means no ssh round-trip per registered peer."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    index = tmp_path / "store-index"
    index.mkdir()
    monkeypatch.setenv("STORE_INDEX_DIR", str(index))
    target = tmp_path / "devel"
    target.mkdir()
    (index / "qemu-devel-11.0.0-abc123def456").symlink_to(target)

    def explode(*_args, **_kwargs):
        raise AssertionError("swept the peers for a layer this host already has")

    monkeypatch.setattr(fetch_identity.store, "fetch_from_peers", explode)
    assert fetch_identity._fetch_devel(tmp_path, "11.0.0-abc123def456") == {
        "devel_fetched": False
    }
