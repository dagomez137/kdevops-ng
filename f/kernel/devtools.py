# SPDX-License-Identifier: copyleft-next-0.3.1
"""Generate developer-tooling artifacts after a kernel build (optional, default on).

The direct-boot path builds out-of-tree, so the artifacts IDEs and debuggers want
land in the build dir (or not at all) instead of the source tree where the tools
look. Each artifact below is independently toggled and on by default, mirroring the
kdevops direct-boot dev-tooling tasks:

  - compile_commands.json: clangd/cscope/IDE C index. `gen_compile_commands.py`
    walks the build's .cmd files and writes the index to the source tree root, where
    the consumers expect it (a normal build leaves the .cmd files in the build dir).
  - vmlinux-gdb.py + lx-*: kernel GDB helpers materialized in the build dir by
    `make scripts_gdb`; a normal build does not produce them. Works against any
    config (the helpers are pure Python around vmlinux symbols).
  - rust-project.json: rust-analyzer index, generated only when CONFIG_RUST=y.
    `make rust-analyzer` writes it to the build dir; copy it to the source tree where
    the LSP looks, the same way compile_commands.json indexes the C source.

Equivalent bash, run inside the nixos-flake build devShell:

    python3 scripts/clang-tools/gen_compile_commands.py --directory "$build" --output "$worktree/compile_commands.json"
    make --directory="$worktree" O="$build" scripts_gdb
    grep --quiet '^CONFIG_RUST=y$' "$build/.config" && {
        make --directory="$worktree" O="$build" rust-analyzer
        cp "$build/rust-project.json" "$worktree/rust-project.json"
    }
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

from f.common.devshell import DevShell


def main(
    worktree: str,
    build_dir: str,
    compile_commands: bool = True,
    scripts_gdb: bool = True,
    rust_analyzer: bool = True,
    make_flags: str = "",
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    build = Path(build_dir)
    wt = Path(worktree)
    flag_args = shlex.split(make_flags)
    base = ["make", f"--directory={worktree}", f"O={build}"]

    shell = DevShell(workers)
    out: dict = {"compile_commands": None, "vmlinux_gdb": None, "rust_project": None}

    if compile_commands:
        cc = wt / "compile_commands.json"
        shell.run("python3", str(wt / "scripts/clang-tools/gen_compile_commands.py"),
                  "--directory", str(build), "--output", str(cc), cwd=worktree)
        out["compile_commands"] = str(cc)

    if scripts_gdb:
        shell.run(*base, *flag_args, "scripts_gdb")
        out["vmlinux_gdb"] = str(build / "vmlinux-gdb.py")

    if rust_analyzer:
        config = build / ".config"
        if config.is_file() and "CONFIG_RUST=y\n" in config.read_text():
            shell.run(*base, *flag_args, "rust-analyzer")
            src = build / "rust-project.json"
            if src.is_file():
                dst = wt / "rust-project.json"
                shutil.copyfile(src, dst)
                out["rust_project"] = str(dst)
        else:
            print("CONFIG_RUST not enabled; skipping rust-analyzer", flush=True)

    print(f"dev artifacts: {out}", flush=True)
    return out
