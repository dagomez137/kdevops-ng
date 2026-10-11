# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the boot step's pure helpers (`f.qsu.boot`)."""

import os

import pytest

from f.qsu import boot


def test_atomic_write_creates_parents_and_content(tmp_path):
    path = tmp_path / "config.d/demo.conf"
    boot._atomic_write(path, "Host demo\n")
    assert path.read_text() == "Host demo\n"


def test_atomic_write_sets_the_mode(tmp_path):
    path = tmp_path / "key"
    boot._atomic_write(path, "secret\n", 0o600)
    assert path.stat().st_mode & 0o777 == 0o600


def test_atomic_write_leaves_no_temp_files(tmp_path):
    path = tmp_path / "demo.conf"
    boot._atomic_write(path, "x\n")
    assert os.listdir(tmp_path) == ["demo.conf"]


def test_write_ssh_alias_needs_a_vsock_cid(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    (tmp_path / "ssh").mkdir()
    (tmp_path / "ssh/id_ed25519").write_text("key\n")
    assert boot._write_ssh_alias("demo", None) is None


def test_write_ssh_alias_needs_the_managed_key(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    assert boot._write_ssh_alias("demo", 105) is None


def test_write_ssh_alias_writes_the_vsock_host_block(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    (tmp_path / "ssh").mkdir()
    priv = tmp_path / "ssh/id_ed25519"
    priv.write_text("key\n")
    out = boot._write_ssh_alias("demo", 105)
    conf = tmp_path / "ssh/config.d/demo.conf"
    assert out == str(conf)
    lines = conf.read_text().splitlines()
    assert lines[0] == "Host demo"
    assert "    HostName vsock/105" in lines
    assert f"    IdentityFile {priv}" in lines
    assert "    User root" in lines
    assert conf.read_text().endswith("\n")


def test_boot_refuses_without_the_root_granted_devices(tmp_path):
    granted = tmp_path / "kvm"
    granted.write_text("")
    boot._check_host_access((str(granted),))
    with pytest.raises(RuntimeError, match="requirements.rst"):
        boot._check_host_access((str(granted), str(tmp_path / "vhost-vsock")))
