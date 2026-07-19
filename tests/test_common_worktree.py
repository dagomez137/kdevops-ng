# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the worktree path addressing and guards (`f.common.worktree`)."""

from pathlib import Path

import pytest

from f.common import worktree

ENV = (
    "WORKBENCH_DIR",
    "WORKERS_DIR",
    "WORKTREES_DIR",
    "SYSTEM_DIR",
    "VENDOR_DIR",
    "WORKER_INDEX",
)


class _StubGit:
    """In-process stand-in for the flake `git` so no subprocess ever runs."""

    def __init__(self, *args, **kwargs):
        pass

    def capture(self, *args, check=True):
        if "--get-all" in args:
            return "*\n"
        if "--verify" in args:
            return "abc123\n" if args[-1].startswith("refs/tags/") else ""
        if args[-1] == "HEAD":
            return "abc123\n"
        return ""

    def run(self, *args, check=True):
        if "worktree" in args and "add" in args:
            Path(args[-2]).mkdir(parents=True, exist_ok=True)
        return 0

    def ok(self, *args):
        return "--git-dir" not in args


@pytest.fixture
def build_area(monkeypatch, tmp_path):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    monkeypatch.setenv("VENDOR_DIR", str(tmp_path / "vendor"))
    (tmp_path / "system/bare/linux.git/objects").mkdir(parents=True)
    (tmp_path / "vendor/nixos-flake").mkdir(parents=True)
    (tmp_path / "vendor/nixos-flake/flake.nix").write_text("")
    monkeypatch.setattr(worktree, "Git", _StubGit)
    return tmp_path


def test_developer_worktree_roots_under_its_group(build_area):
    result = worktree.prepare(
        project="linux", ref="v6.9", developer=True, worktree_group="lace"
    )
    assert result["worktree"] == str(build_area / "lace" / "linux")
    assert result["worktree_group"] == "lace"
    assert "worker" not in result
    assert result["commit"] == "abc123"
    assert result["label"] == "vanilla"


def test_worktrees_dir_relocates_the_developer_groups(build_area, monkeypatch):
    monkeypatch.setenv("WORKTREES_DIR", str(build_area / "trees"))
    result = worktree.prepare(project="linux", ref="v6.9", developer=True)
    assert result["worktree"] == str(build_area / "trees" / "vanilla" / "linux")


def test_worker_worktree_roots_under_the_fixed_main_group(build_area, monkeypatch):
    monkeypatch.setenv("WORKER_INDEX", "3")
    result = worktree.prepare(
        project="linux", ref="v6.9", extra_dirs=("build", "destdir")
    )
    expected = build_area / "workers" / "3" / "main" / "linux"
    assert result["worktree"] == str(expected)
    assert result["worker"] == "3"
    assert result["build_dir"] == str(expected / "build")
    assert result["destdir"] == str(expected / "destdir")


def test_prepare_rejects_a_flag_shaped_ref():
    with pytest.raises(ValueError, match="invalid ref"):
        worktree.prepare(project="linux", ref="--exec=evil")


def test_validate_group_accepts_a_plain_component():
    worktree.validate_group("vanilla")
    worktree.validate_group("b4-series.v2")


@pytest.mark.parametrize("group", ["", ".", "..", "-g", "a b", "a/b", "a\tb"])
def test_validate_group_rejects_path_and_flag_shapes(group):
    with pytest.raises(ValueError, match="invalid worktree-group"):
        worktree.validate_group(group)


@pytest.mark.parametrize("group", ["system", "workers"])
def test_validate_group_rejects_reserved_siblings(group):
    with pytest.raises(ValueError, match="reserved"):
        worktree.validate_group(group)
