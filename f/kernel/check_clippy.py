# SPDX-License-Identifier: copyleft-next-0.3.1
"""Rebuild the Rust code under Clippy, the Rust linter.

Documentation/rust/general-information.rst: "some extra lints and analyses
are available via `clippy`, a Rust linter. To enable it, pass `CLIPPY=1` to
the same invocation used for compilation". The changed command line rebuilds
every Rust object through `clippy-driver`. The same page warns that "Clippy
may change code generation, thus it should not be enabled while building a
production kernel"; the next build without `CLIPPY=1` rebuilds those objects
again, for the same reason. `scope=series` counts the lints in the series'
own files; `scope=worktree` counts all of them. An error fails the check; warnings
are counted.

Equivalent bash, run inside the nixos-flake check-kernel devShell:

    make --directory="$worktree" O="$build_dir" $make_flags CLIPPY=1 \\
        KCFLAGS=-Wno-error KRUSTFLAGS=--cap-lints=warn \\
        | tee "$check_dir/clippy.log"
"""

from __future__ import annotations

from f.kernel.checks import (
    check_dir,
    counting,
    in_files,
    make,
    result,
    rust_diagnostics,
    shell,
)


def main(
    worktree: str,
    build_dir: str,
    files: list[str],
    scope: str = "series",
    make_flags: str = "",
) -> dict:
    log = check_dir() / "clippy.log"
    rc = shell().tee(
        *make(worktree, build_dir, counting(make_flags), "CLIPPY=1"), log=log
    )
    findings = rust_diagnostics(log.read_text(), worktree)
    if scope == "series":
        findings = in_files(findings, files)
    return result("clippy", findings, log, scope=scope, failed=rc != 0)
