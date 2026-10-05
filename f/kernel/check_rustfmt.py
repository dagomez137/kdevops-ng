# SPDX-License-Identifier: copyleft-next-0.3.1
"""Check that the Rust sources are formatted as `rustfmt` would.

Documentation/rust/coding-guidelines.rst: "The code should be formatted using
`rustfmt`", and "It is also possible to check if everything is formatted
(printing a diff otherwise), for instance for a CI, with" `make LLVM=1
rustfmtcheck`. Each hunk is a finding, and formatting is not a
judgment call, so any hunk fails the check. `scope=series` counts the hunks in
the series' own files; `scope=worktree` counts all of them. A non-zero exit with
no hunk at all means `rustfmt` never ran, which fails the check too.

Equivalent bash, run inside the nixos-flake check-kernel devShell:

    make --directory="$worktree" O="$build_dir" $make_flags rustfmtcheck \\
        | tee "$check_dir/rustfmt.log"
"""

from __future__ import annotations

from f.kernel.checks import check_dir, in_files, make, result, rustfmt_diffs, shell


def main(
    worktree: str,
    build_dir: str,
    files: list[str],
    scope: str = "series",
    make_flags: str = "",
) -> dict:
    log = check_dir() / "rustfmt.log"
    rc = shell().tee(*make(worktree, build_dir, make_flags, "rustfmtcheck"), log=log)
    every = rustfmt_diffs(log.read_text(), worktree)
    findings = in_files(every, files) if scope == "series" else every
    return result("rustfmt", findings, log, scope=scope, failed=rc != 0 and not every)
