# SPDX-License-Identifier: copyleft-next-0.3.1
"""Count the compiler warnings of a `W=1` build.

Documentation/process/submit-checklist.rst asks that the kernel builds with "No
`gcc` warnings/errors, no linker warnings/errors", and that "Newly-added code
has been compiled with `gcc -W`". `W=1` is kbuild's level for warnings that
"may be relevant and do not occur too often" (`make help`), and it also runs
kernel-doc over every compiled C file.

`W=1` is this check's own `make` flag, not the Build's, so the Build identity is
unchanged; the rebuilt objects differ only in their recorded command line, and
the next build of that identity recompiles them. `scope=series` removes the
series' own objects and rebuilds them, so a warm Build cannot skip them, and
counts the warnings in the series' files. `scope=worktree` rebuilds every
object, which the changed command line forces anyway, and counts every
warning. Warnings never fail the check; an object that does not build does.

Equivalent bash, run inside the nixos-flake check-kernel devShell:

    rm --force "$build_dir"/lib/xarray.o                  # scope=series
    make --directory="$worktree" O="$build_dir" $make_flags W=1 \\
        KCFLAGS=-Wno-error KRUSTFLAGS=--cap-lints=warn lib/xarray.o \\
        | tee "$check_dir/warnings.log"
"""

from __future__ import annotations

import shlex
from pathlib import Path

from f.kernel.checks import (
    check_dir,
    counting,
    diagnostics,
    in_files,
    kernel_objects,
    make,
    result,
    shell,
)


def main(
    worktree: str,
    build_dir: str,
    files: list[str],
    scope: str = "series",
    make_flags: str = "",
) -> dict:
    objects: list[str] = []
    if scope == "series":
        objects = kernel_objects(files, build_dir)
        if not objects:
            print("the series changes no C file the Build compiles", flush=True)
            return result("W=1", [], None, scope=scope, objects=[])
        for obj in objects:
            built = Path(build_dir) / obj
            if built.exists():
                built.unlink()
                print(f"removed {built}", flush=True)
    flags = counting(make_flags)
    if not any(t.startswith("W=") for t in shlex.split(make_flags)):
        flags = f"{flags} W=1"
    log = check_dir() / "warnings.log"
    rc = shell().tee(*make(worktree, build_dir, flags, *objects), log=log)
    findings = diagnostics(log.read_text(), worktree)
    if scope == "series":
        findings = in_files(findings, files)
    return result("W=1", findings, log, scope=scope, objects=objects, failed=rc != 0)
