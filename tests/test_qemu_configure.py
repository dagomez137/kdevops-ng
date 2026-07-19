# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the QEMU configure step's pure validation (`f.qemu.configure`)."""

import pytest

from f.qemu import configure


def test_toolchain_table_maps_both_drivers():
    assert configure._TOOLCHAIN == {
        "gcc": ("gcc", "g++"),
        "clang": ("clang", "clang++"),
    }


def test_unknown_compiler_is_rejected_before_any_work(tmp_path):
    with pytest.raises(ValueError, match="gcc or clang"):
        configure.main(
            worktree=str(tmp_path),
            build_dir=str(tmp_path),
            destdir=str(tmp_path),
            compiler="tcc",
        )
