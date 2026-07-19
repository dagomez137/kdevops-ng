# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the kernel make-flags composition (f.kernel.build_flags)."""

import shlex

import pytest

from f.kernel import build_flags

REPRO = [
    "KBUILD_BUILD_TIMESTAMP=Sun Aug 25 20:57:08 UTC 1991",
    "KBUILD_BUILD_USER=kdevops",
    "KBUILD_BUILD_HOST=kdevops",
    "LOCALVERSION=",
]


def _tokens(result):
    return shlex.split(result["make_flags"])


def test_unknown_compiler_raises():
    with pytest.raises(ValueError, match="gcc or clang"):
        build_flags.main(compiler="tcc")


def test_bare_gcc_build_yields_no_flags():
    out = build_flags.main(compiler="gcc", reproducible=False, ccache=False)
    assert out == {"make_flags": "", "ccache_conf": None}


def test_reproducible_flags_use_the_fixed_timestamp():
    out = build_flags.main(reproducible=True, ccache=False)
    assert _tokens(out) == REPRO


def test_timestamp_from_commit_without_a_commit_keeps_the_fixed_one():
    out = build_flags.main(reproducible=True, ccache=False, timestamp_from_commit=True)
    assert _tokens(out) == REPRO


def test_prefix_map_derives_from_the_common_parent(tmp_path):
    worktree = tmp_path / "linux"
    build = worktree / "build"
    out = build_flags.main(
        reproducible=True, ccache=False, worktree=str(worktree), build_dir=str(build)
    )
    prefix_map = f"-fdebug-prefix-map={worktree}/="
    assert _tokens(out) == REPRO + [f"KCFLAGS={prefix_map}", f"KAFLAGS={prefix_map}"]


def test_prefix_map_folds_into_user_kcflags(tmp_path):
    worktree = tmp_path / "linux"
    build = worktree / "build"
    out = build_flags.main(
        reproducible=True,
        ccache=False,
        worktree=str(worktree),
        build_dir=str(build),
        make_flags="KCFLAGS=-O3",
    )
    prefix_map = f"-fdebug-prefix-map={worktree}/="
    assert _tokens(out) == REPRO + [
        f"KAFLAGS={prefix_map}",
        f"KCFLAGS=-O3 {prefix_map}",
    ]


def test_extra_make_flags_pass_through():
    out = build_flags.main(reproducible=False, ccache=False, make_flags="W=1 LLVM=1")
    assert _tokens(out) == ["W=1", "LLVM=1"]


def test_ccache_gcc_wraps_cc_and_writes_the_conf(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    monkeypatch.setenv("CCACHE_DIR", str(tmp_path / "ccache"))
    (tmp_path / "workers").mkdir()
    out = build_flags.main(reproducible=False, ccache=True, ccache_max_size=7)
    assert _tokens(out) == ["CC=ccache gcc"]
    assert out["ccache_conf"] == str(tmp_path / "ccache" / "ccache.conf")
    text = (tmp_path / "ccache" / "ccache.conf").read_text()
    assert "max_size = 7.0 GiB" in text
    assert f"cache_dir = {tmp_path / 'ccache'}" in text


def test_ccache_max_size_below_one_raises():
    with pytest.raises(ValueError, match=">= 1 GiB"):
        build_flags.main(reproducible=False, ccache=True, ccache_max_size=0)


def test_merge_prefix_map_emits_missing_vars():
    parts = []
    merged = build_flags._merge_prefix_map([], "-fdebug-prefix-map=/p/=", parts)
    assert merged == []
    assert parts == [
        "KCFLAGS=-fdebug-prefix-map=/p/=",
        "KAFLAGS=-fdebug-prefix-map=/p/=",
    ]


def test_merge_prefix_map_folds_both_user_vars():
    parts = []
    merged = build_flags._merge_prefix_map(
        ["KCFLAGS=-O2", "KAFLAGS=-g"], "-fdebug-prefix-map=/p/=", parts
    )
    assert merged == [
        "KCFLAGS=-O2 -fdebug-prefix-map=/p/=",
        "KAFLAGS=-g -fdebug-prefix-map=/p/=",
    ]
    assert parts == []
