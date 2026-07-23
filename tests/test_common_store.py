# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the store index's pure reads (`f.common.store`)."""

import os

from f.common import store

ENV = ("STORE_INDEX_DIR", "SYSTEM_DIR", "WORKBENCH_DIR", "WORKERS_DIR")


def _clear_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_pure_reads_survive_an_unresolvable_env(monkeypatch):
    _clear_env(monkeypatch)
    assert store.list_index("kernel-") == []
    assert store.local_path("kernel-x") is None
    assert store.latest_index("kernel-") is None


def test_index_reads_resolve_live_entries_only(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    index = tmp_path / "store-index"
    index.mkdir()
    monkeypatch.setenv("STORE_INDEX_DIR", str(index))
    target = tmp_path / "kernel-tree"
    target.mkdir()
    (index / "kernel-7.0.0-test").symlink_to(target)
    (index / "kernel-9.9.9-gone").symlink_to(tmp_path / "collected")

    assert store.list_index("kernel-") == ["kernel-7.0.0-test"]
    assert store.local_path("kernel-7.0.0-test") == os.path.realpath(target)
    assert store.local_path("kernel-9.9.9-gone") is None
    assert store.latest_index("kernel-") == "kernel-7.0.0-test"
    assert store.list_index("qemu-") == []


def test_registered_peers_reads_the_registry(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    assert store.registered_peers() == []
    (tmp_path / "peers").write_text("hostb /custom/index\nhostc\n\n")
    assert store.registered_peers() == [
        {"host": "hostb", "index": "/custom/index"},
        {"host": "hostc", "index": store.DEFAULT_PEER_INDEX},
    ]


def test_run_layers_never_offer_a_devel_layer(monkeypatch, tmp_path):
    """A picker or a reuse fallback must not surface an artifact nothing can boot."""
    _clear_env(monkeypatch)
    index = tmp_path / "store-index"
    index.mkdir()
    monkeypatch.setenv("STORE_INDEX_DIR", str(index))
    live = tmp_path / "live"
    live.mkdir()
    for name in (
        "qemu-11.0.0-vanilla-aaaaaaaaaaaa",
        "qemu-devel-11.0.0-vanilla-aaaaaaaaaaaa",
        "kernel-7.1.0-vanilla-bbbbbbbbbbbb",
        "kernel-devel-7.1.0-vanilla-bbbbbbbbbbbb",
        # A label that merely contains the word is a run layer, not a devel layer:
        # the exclusion is anchored at the project name.
        "qemu-11.0.0-devel-notes-cccccccccccc",
    ):
        (index / name).symlink_to(live)

    assert store.list_run_layers("qemu") == [
        "qemu-11.0.0-devel-notes-cccccccccccc",
        "qemu-11.0.0-vanilla-aaaaaaaaaaaa",
    ]
    assert store.list_run_layers("kernel") == ["kernel-7.1.0-vanilla-bbbbbbbbbbbb"]

    # The devel layer is the newest entry under the prefix, which is exactly how the
    # plain form auto-picked one for a reuse boot.
    os.utime(
        index / "qemu-devel-11.0.0-vanilla-aaaaaaaaaaaa",
        (2**31, 2**31),
        follow_symlinks=False,
    )
    assert store.latest_index("qemu-") == "qemu-devel-11.0.0-vanilla-aaaaaaaaaaaa"
    assert store.latest_run_layer("qemu") != "qemu-devel-11.0.0-vanilla-aaaaaaaaaaaa"
