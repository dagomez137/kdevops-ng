# SPDX-License-Identifier: copyleft-next-0.3.1
"""Run the Rust host tests.

Documentation/rust/testing.rst: "These are userspace tests that can be built
and run in the host (i.e. the one that performs the kernel build) using the
`rusttest` Make target". A failing test fails the check; the totals come from
each test binary's `test result:` line. The doctests that need a running
kernel are the KUnit suite's (`rust_doctests_kernel`), not this step's.

Equivalent bash, run inside the nixos-flake check-kernel devShell:

    make --directory="$worktree" O="$build_dir" $make_flags rusttest \\
        KCFLAGS=-Wno-error KRUSTFLAGS=--cap-lints=warn \\
        | tee "$check_dir/rusttest.log"
"""

from __future__ import annotations

from f.kernel.checks import (
    check_dir,
    counting,
    make,
    result,
    rust_diagnostics,
    rusttest_totals,
    shell,
)


def main(worktree: str, build_dir: str, make_flags: str = "") -> dict:
    log = check_dir() / "rusttest.log"
    rc = shell().tee(
        *make(worktree, build_dir, counting(make_flags), "rusttest"), log=log
    )
    text = log.read_text()
    totals = rusttest_totals(text)
    return result(
        "rusttest",
        rust_diagnostics(text, worktree),
        log,
        tests_passed=totals["passed"],
        tests_failed=totals["failed"],
        failed=rc != 0 or totals["failed"] > 0,
    )
