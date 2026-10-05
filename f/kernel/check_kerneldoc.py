# SPDX-License-Identifier: copyleft-next-0.3.1
"""Check the kernel-doc comments of the series' C sources and headers.

Documentation/process/submit-checklist.rst: "Include kernel-doc to document
global kernel APIs." A `W=1` build already runs kernel-doc over each C file it
compiles, but not over headers and without `-Wall`.
Documentation/doc-guide/kernel-doc.rst: running the tool "without actual
output generation may be used to verify proper formatting of the documentation
comments"; `--none` drops the output and `-Wall` turns on every warning.
`scope=series` reads the C and header files
the series touches; `scope=worktree` hands kernel-doc every top-level source
directory, which it walks itself. Warnings are counted.

Equivalent bash, run inside the nixos-flake check-kernel devShell, from the
Worktree root:

    scripts/kernel-doc --none -Wall lib/xarray.c include/linux/xarray.h \\
        | tee "$check_dir/kernel-doc.log"
"""

from __future__ import annotations

from f.common.devshell import Git
from f.kernel.checks import (
    NOT_KERNEL_OBJECTS,
    check_dir,
    diagnostics,
    in_files,
    result,
    shell,
)


def _sources(worktree: str, files: list[str], scope: str) -> list[str]:
    if scope == "series":
        return [
            f
            for f in files
            if f.endswith((".c", ".h")) and not f.startswith(NOT_KERNEL_OBJECTS)
        ]
    top = Git().capture("-C", worktree, "ls-tree", "--name-only", "-d", "HEAD")
    return [
        d
        for d in top.split()
        if f"{d}/" not in NOT_KERNEL_OBJECTS and d not in ("LICENSES", "usr")
    ]


def main(worktree: str, files: list[str], scope: str = "series") -> dict:
    sources = _sources(worktree, files, scope)
    if not sources:
        print("the series changes no C source or header", flush=True)
        return result("kernel-doc", [], None, scope=scope)
    log = check_dir() / "kernel-doc.log"
    rc = shell().tee(
        "scripts/kernel-doc", "--none", "-Wall", *sources, log=log, cwd=worktree
    )
    findings = diagnostics(log.read_text(), worktree)
    if scope == "series":
        findings = in_files(findings, files)
    return result("kernel-doc", findings, log, scope=scope, failed=rc != 0)
