# SPDX-License-Identifier: copyleft-next-0.3.1
"""Check that the devShell's Rust toolchain is one the kernel accepts.

Documentation/rust/quick-start.rst: `make LLVM=1 rustavailable` "triggers the
same logic used by Kconfig to determine whether `RUST_IS_AVAILABLE` should be
enabled; but it also explains why not if that is the case." A Build configured
with `CONFIG_RUST=y` already passed that test once, so a failure here means
the toolchain changed since; the other Rust checks would fail with it.

Equivalent bash, run inside the nixos-flake check-kernel devShell:

    make --directory="$worktree" O="$build_dir" $make_flags rustavailable \\
        | tee "$check_dir/rustavailable.log"
"""

from __future__ import annotations

from f.kernel.checks import check_dir, make, result, shell


def main(worktree: str, build_dir: str, make_flags: str = "") -> dict:
    log = check_dir() / "rustavailable.log"
    rc = shell().tee(*make(worktree, build_dir, make_flags, "rustavailable"), log=log)
    return result("rustavailable", [], log, failed=rc != 0)
