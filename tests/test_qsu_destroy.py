# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the destroy step's pure helpers (`f.qsu.destroy`)."""

import pytest

from f.qsu import destroy


def test_rm_removes_a_file(tmp_path):
    path = tmp_path / "demo.env"
    path.write_text("x\n")
    assert destroy._rm(path) == str(path)
    assert not path.exists()


def test_rm_removes_a_directory_tree(tmp_path):
    dropin = tmp_path / "qemu-system@demo.service.d"
    dropin.mkdir()
    (dropin / "override.conf").write_text("x\n")
    assert destroy._rm(dropin) == str(dropin)
    assert not dropin.exists()


def test_rm_removes_a_dangling_symlink(tmp_path):
    link = tmp_path / "gone"
    link.symlink_to(tmp_path / "collected")
    assert destroy._rm(link) == str(link)
    assert not link.is_symlink()


def test_rm_unlinks_a_symlink_to_a_directory_without_following(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep").write_text("x\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert destroy._rm(link) == str(link)
    assert not link.is_symlink()
    assert (target / "keep").exists()


def test_rm_missing_path_reports_nothing(tmp_path):
    assert destroy._rm(tmp_path / "absent") is None


def test_targets_cover_every_per_vm_artefact(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path / "system"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg, workers = tmp_path / "cfg", tmp_path / "workers"
    user, vfsd = cfg / "user", cfg / "virtiofsd"
    user.mkdir(parents=True)
    vfsd.mkdir(parents=True)
    (user / "virtiofsd@demo-store.service.d").mkdir()
    (vfsd / "demo-store.env").write_text("x\n")
    (user / "virtiofsd@other-store.service.d").mkdir()
    (vfsd / "other-store.env").write_text("x\n")

    names = [p.name for p in destroy._targets(cfg, workers, "demo")]
    assert names[:2] == ["demo.env", "qemu-system@demo.service.d"]
    assert "demo.vars.json" in names
    assert "demo.conf" in names
    assert "virtiofsd@demo-store.service.d" in names
    assert "demo-store.env" in names
    assert not [n for n in names if n.startswith("other")]


def test_main_refuses_an_empty_selection():
    with pytest.raises(ValueError, match="at least one VM"):
        destroy.main([])
