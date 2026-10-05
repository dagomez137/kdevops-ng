# SPDX-License-Identifier: copyleft-next-0.3.1
"""Run Sparse, the kernel's semantic checker, over the Build.

Documentation/process/submit-checklist.rst: "Check cleanly with sparse."
Documentation/dev-tools/sparse.rst: "use "make C=2" to run sparse on the files
whether they need to be recompiled or not. The latter is a fast way to check
the whole tree if you have already built it." `scope=series` names the
series' own objects as the goals and counts the findings in the series' files;
`scope=worktree` checks every file and counts everything.

kbuild only warns when `scripts/checker-valid.sh` rejects the installed `sparse`
and then builds with no checker at all, so that text fails the step rather
than reading as a clean run. The check-kernel devShell carries a Sparse new
enough for current kernels.

Equivalent bash, run inside the nixos-flake check-kernel devShell:

    make --directory="$worktree" O="$build_dir" C=2 $make_flags [lib/xarray.o ...] \\
        | tee "$check_dir/sparse.log"
"""

from __future__ import annotations

from f.kernel.checks import (
    check_dir,
    diagnostics,
    in_files,
    kernel_objects,
    make,
    result,
    shell,
)

_REJECTED = "specified, but sparse is not available or not up to date"


def main(
    worktree: str,
    build_dir: str,
    files: list[str],
    scope: str = "series",
    make_flags: str = "",
) -> dict:
    objects = kernel_objects(files, build_dir) if scope == "series" else []
    if scope == "series" and not objects:
        print("the series changes no C file the Build compiles", flush=True)
        return result("sparse", [], None, scope=scope, objects=[])
    log = check_dir() / "sparse.log"
    rc = shell().tee(*make(worktree, build_dir, make_flags, "C=2", *objects), log=log)
    text = log.read_text()
    if _REJECTED in text:
        raise RuntimeError("kbuild rejected the devShell's sparse; nothing was checked")
    findings = diagnostics(text, worktree)
    if scope == "series":
        findings = in_files(findings, files)
    return result("sparse", findings, log, scope=scope, objects=objects, failed=rc != 0)
