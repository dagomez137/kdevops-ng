# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the destroy step's pure removal helper (`f.qsu.destroy`)."""

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
