# SPDX-License-Identifier: copyleft-next-0.3.1
"""Run clang-tidy and the Clang Static Analyzer over a Clang Build.

The kernel's `make help` lists both: "clang-tidy - Check with clang-tidy" and
"clang-analyzer - Check with clang static analyzer". Each reads the build's
`compile_commands.json` through `scripts/clang-tools/run-clang-tools.py`, so
the Build must use Clang (`CONFIG_CC_IS_CLANG`). `scope=series`
hands the tool a copy of the database holding only the series' C files;
`scope=worktree` hands it the whole database. An error fails the check; warnings
are counted.

Equivalent bash, run inside the nixos-flake check-kernel devShell:

    make --directory="$worktree" O="$build_dir" $make_flags compile_commands.json
    python3 "$worktree"/scripts/clang-tools/run-clang-tools.py clang-tidy \\
        "$check_dir/compile_commands.json" | tee "$check_dir/clang-tidy.log"
"""

from __future__ import annotations

import json
from pathlib import Path

from f.kernel.checks import (
    check_dir,
    config_has,
    diagnostics,
    in_files,
    make,
    result,
    shell,
    worktree_path,
)

TOOLS = ("clang-tidy", "clang-analyzer")


def main(
    worktree: str,
    build_dir: str,
    files: list[str],
    tools: list[str] | None = None,
    scope: str = "series",
    make_flags: str = "",
) -> dict:
    tools = list(tools or TOOLS)
    if not config_has(build_dir, "CONFIG_CC_IS_CLANG"):
        raise ValueError("clang-tidy and clang-analyzer need a Build that uses Clang")
    sh = shell()
    sh.run(*make(worktree, build_dir, make_flags, "compile_commands.json"))
    database = Path(build_dir) / "compile_commands.json"
    if scope == "series":
        wanted = set(files)
        entries = [
            e
            for e in json.loads(database.read_text())
            if worktree_path(e["file"], worktree) in wanted
        ]
        if not entries:
            print("the series changes no C file in the build", flush=True)
            return result("clang", [], None, scope=scope, tools=tools)
        database = check_dir() / "compile_commands.json"
        database.write_text(json.dumps(entries, indent=1))
        print(f"wrote {database} ({len(entries)} entries)", flush=True)

    findings, failed, logs = [], False, {}
    for tool in tools:
        log = check_dir() / f"{tool}.log"
        rc = sh.tee(
            "python3",
            f"{worktree}/scripts/clang-tools/run-clang-tools.py",
            tool,
            str(database),
            log=log,
            cwd=build_dir,
        )
        failed |= rc != 0
        logs[tool] = str(log)
        findings += diagnostics(log.read_text(), worktree)
    if scope == "series":
        findings = in_files(findings, files)
    return result(
        "clang", findings, None, scope=scope, tools=tools, logs=logs, failed=failed
    )
