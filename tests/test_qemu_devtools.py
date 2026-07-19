# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the compile_commands.json placement step (`f.qemu.devtools`)."""

from f.qemu import devtools


def test_disabled_is_a_no_op(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "compile_commands.json").write_text("[]")
    assert devtools.main(str(tmp_path), str(build), compile_commands=False) == {
        "compile_commands": None
    }
    assert not (tmp_path / "compile_commands.json").exists()


def test_missing_index_skips_without_raising(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    assert devtools.main(str(tmp_path), str(build)) == {"compile_commands": None}


def test_index_is_copied_to_the_source_root(tmp_path):
    worktree = tmp_path / "src"
    build = tmp_path / "build"
    worktree.mkdir()
    build.mkdir()
    (build / "compile_commands.json").write_text('[{"file": "a.c"}]')
    out = devtools.main(str(worktree), str(build))
    dest = worktree / "compile_commands.json"
    assert out == {"compile_commands": str(dest)}
    assert dest.read_text() == '[{"file": "a.c"}]'
    assert not (worktree / "compile_commands.json.tmp").exists()


def test_copy_overwrites_a_stale_index(tmp_path):
    worktree = tmp_path / "src"
    build = tmp_path / "build"
    worktree.mkdir()
    build.mkdir()
    (worktree / "compile_commands.json").write_text("stale")
    (build / "compile_commands.json").write_text("fresh")
    devtools.main(str(worktree), str(build))
    assert (worktree / "compile_commands.json").read_text() == "fresh"
