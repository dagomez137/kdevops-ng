# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the fstests share renderer (`f.fstests.render_config`)."""

from pathlib import Path

import pytest

from f.fstests import render_config
from f.fstests.common import share_dir

# Three device-agnostic sections, all geometry-valid on a 512-byte-sector device.
CATALOG = """\
[xfs_a]
FSTYP=xfs
MKFS_OPTIONS="-b size=4096"

[xfs_b]
FSTYP=xfs
MKFS_OPTIONS="-b size=1024 -s size=512"

[xfs_c]
FSTYP=xfs
MKFS_OPTIONS="-b size=2048"
"""

DEVICES = [
    {"name": "/dev/vdb", "log_sec": 512},
    {"name": "/dev/vdc", "log_sec": 512},
]


@pytest.fixture
def workers(monkeypatch, tmp_path):
    root = tmp_path / "workbench" / "workers"
    root.mkdir(parents=True)
    monkeypatch.setenv("WORKERS_DIR", str(root))
    return root


def _render(**overrides):
    args = dict(
        vm_name="vm0",
        kernel_version="6.18.0",
        local_config=CATALOG,
        devices=DEVICES,
        logwrites=False,
        groups=["quick"],
    )
    args.update(overrides)
    return render_config.main(**args)


def test_arm_off_writes_only_the_selected_sections(workers):
    out = _render(sections=["xfs_a", "xfs_c"])
    share = Path(out["share_dir"])
    assert out["sections"] == ["xfs_a", "xfs_c"]
    assert out["armed"] == ["xfs_a", "xfs_c"]
    for section in ("xfs_a", "xfs_c"):
        assert (share / f"{section}.config").is_file()
        assert (share / f"{section}.env").is_file()
    # A catalog section that was not selected gets neither file.
    assert not (share / "xfs_b.config").exists()
    assert not (share / "xfs_b.env").exists()


def test_arm_all_lays_down_every_valid_section(workers):
    out = _render(sections=["xfs_a"], arm_all_sections=True)
    share = Path(out["share_dir"])
    # The run set stays the selection; armed lists every valid catalog section.
    assert out["sections"] == ["xfs_a"]
    assert out["armed"] == ["xfs_a", "xfs_b", "xfs_c"]
    for section in ("xfs_a", "xfs_b", "xfs_c"):
        assert (share / f"{section}.config").is_file()
        assert (share / f"{section}.env").is_file()
    # The selected section's env carries the run's args; an armed-only one gets -g auto.
    assert "XFSTESTS_CHECK_ARGS=-g quick -R xunit" in (share / "xfs_a.env").read_text()
    assert "XFSTESTS_CHECK_ARGS=-g auto -R xunit" in (share / "xfs_b.env").read_text()
    assert "XFSTESTS_CHECK_ARGS=-g auto -R xunit" in (share / "xfs_c.env").read_text()


def test_prune_clears_dead_leftovers_and_mirrors_the_catalog(workers):
    share = share_dir("vm0")
    share.mkdir(parents=True)
    # Retired pre-per-section-env leftovers, always dropped.
    (share / "check.env").write_text("HOST_OPTIONS=x\n")
    (share / "local.config").write_text("[old]\n")
    (share / "xfs_a.mkfs").write_text("mkfs\n")
    (share / "xfs_a.xfs_info").write_text("info\n")
    # A stale section that left the catalog, plus a live geometry sidecar to keep.
    (share / "gone.config").write_text("[gone]\n")
    (share / "gone.env").write_text("x\n")
    (share / "xfs_a.geometry.json").write_text("{}\n")

    _render(sections=["xfs_a"], arm_all_sections=True)

    for name in ("check.env", "local.config", "xfs_a.mkfs", "xfs_a.xfs_info"):
        assert not (share / name).exists()
    # arm-all mirrors the catalog: a section no longer present is pruned.
    assert not (share / "gone.config").exists()
    assert not (share / "gone.env").exists()
    # But the geometry sidecar and the armed sections survive.
    assert (share / "xfs_a.geometry.json").is_file()
    assert (share / "xfs_a.config").is_file()
