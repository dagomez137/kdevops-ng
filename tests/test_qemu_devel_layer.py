# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the QEMU devel layer's pure logic (stage filter, relocation)."""

import json
import shutil

import pytest

from f.common import store
from f.qemu import fetch_devel, publish_devel

BUILDER = "/builder/workers/0000/main/qemu"


def _layer(tmp_path):
    """A materialized devel layer carrying a builder's paths, plus its worktree."""
    worktree = tmp_path / "vanilla/qemu"
    build = worktree / "build"
    (build / "linux-headers").mkdir(parents=True)
    (worktree / "linux-headers/asm-x86").mkdir(parents=True)
    (worktree / "VERSION").write_text("11.0.0\n")
    (build / "linux-headers/asm").symlink_to(f"{BUILDER}/linux-headers/asm-x86")
    (build / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": f"{BUILDER}/build",
                    "command": (
                        f"gcc -Iqapi -iquote {BUILDER}/include "
                        f"-ffile-prefix-map={BUILDER}=/qemu -c ../hw/x.c"
                    ),
                    "file": "../hw/x.c",
                    "output": "libsystem.a.p/hw_x.c.o",
                },
                {
                    "directory": f"{BUILDER}/build",
                    "command": f"gcc -isystem {BUILDER}/linux-headers -c ../hw/y.c",
                    "file": "../hw/y.c",
                    "output": "libsystem.a.p/hw_y.c.o",
                },
            ]
        )
    )
    return worktree, build


def test_stage_filter_keeps_only_the_index(tmp_path):
    build = tmp_path / "build"
    (build / "qemu-bundle/bin").mkdir(parents=True)
    (build / "pyvenv").mkdir()
    (build / "qapi").mkdir()
    (build / "ui/pyvenv").mkdir(parents=True)
    for rel in (
        "compile_commands.json",
        "config-host.h",
        "qemu-options.def",
        "qemu-system-x86_64",
        "config-host.mak",
        "qapi/qapi-types.h",
        "qapi/qapi-types.c",
        "qapi/qapi-types.c.o",
        "qapi/qapi-types.c.o.d",
        "ui/pyvenv/keep.h",
        "qemu-bundle/bin/drop.h",
        "pyvenv/drop.h",
    ):
        (build / rel).write_text("")
    (build / "scripts").symlink_to(tmp_path / "nowhere")

    stage = tmp_path / "stage"
    shutil.copytree(
        build,
        stage,
        symlinks=True,
        ignore=store.subset_filter(
            str(build), publish_devel._DEVEL_KEEP, publish_devel._DROP_TREES
        ),
    )

    kept = sorted(str(p.relative_to(stage)) for p in stage.rglob("*"))
    assert kept == [
        "compile_commands.json",
        "config-host.h",
        "qapi",
        "qapi/qapi-types.c",
        "qapi/qapi-types.h",
        "qemu-options.def",
        "scripts",
        "ui",
        "ui/pyvenv",
        # A drop tree is dropped only at the root, so a nested namesake survives.
        "ui/pyvenv/keep.h",
    ]


def test_relocate_rewrites_the_index_and_the_symlinks(tmp_path):
    worktree, build = _layer(tmp_path)

    result = fetch_devel.relocate(build, worktree)

    assert result["entries"] == 2
    assert result["relinked"] == 1
    index = build / "compile_commands.json"
    text = index.read_text()
    assert BUILDER not in text
    entries = json.loads(text)
    assert entries[0]["directory"] == str(build)
    # The builder path is embedded several times inside one command string.
    assert f"-iquote {worktree}/include" in entries[0]["command"]
    assert f"-ffile-prefix-map={worktree}=/qemu" in entries[0]["command"]
    assert f"-isystem {worktree}/linux-headers" in entries[1]["command"]
    # The load-bearing link: `-isystem linux-headers` reaches the target headers.
    asm = build / "linux-headers/asm"
    assert asm.is_symlink() and asm.resolve() == (worktree / "linux-headers/asm-x86")
    # clangd indexes the source root, so the rewritten index lands there too.
    root = worktree / "compile_commands.json"
    assert result["compile_commands"] == str(root)
    assert json.loads(root.read_text()) == entries
    assert not list(build.glob("*.tmp"))


def test_relocate_survives_a_layer_with_no_index(tmp_path):
    worktree, build = _layer(tmp_path)
    (build / "compile_commands.json").unlink()

    result = fetch_devel.relocate(build, worktree)

    assert result == {"compile_commands": None, "entries": 0, "relinked": 0}
    assert not (worktree / "compile_commands.json").exists()


def test_relocate_refuses_an_index_naming_no_build_dir(tmp_path):
    worktree, build = _layer(tmp_path)
    (build / "compile_commands.json").write_text(json.dumps([{"file": "../hw/x.c"}]))

    with pytest.raises(ValueError, match="names no build directory"):
        fetch_devel.relocate(build, worktree)
