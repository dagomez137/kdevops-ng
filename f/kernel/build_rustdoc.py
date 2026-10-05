# SPDX-License-Identifier: copyleft-next-0.3.1
"""Build the Rust code documentation.

Documentation/rust/general-information.rst: "Rust kernel code is documented
using `rustdoc`", and generating them locally "has the added advantage that
they will be tailored to the particular kernel configuration used", with "the
`rustdoc` target with the same invocation used for compilation". They land in
`Documentation/output/rust/rustdoc/` in the Build. A failed build fails
the check; `rustdoc`'s warnings (broken intra-doc links, missing docs) are
counted. The build covers the whole configured crate set whatever the scope.

Equivalent bash, run inside the nixos-flake check-kernel devShell:

    make --directory="$worktree" O="$build_dir" $make_flags rustdoc \\
        KCFLAGS=-Wno-error KRUSTFLAGS=--cap-lints=warn \\
        | tee "$check_dir/rustdoc.log"
"""

from __future__ import annotations

from f.kernel.checks import check_dir, counting, make, result, rust_diagnostics, shell


def main(worktree: str, build_dir: str, make_flags: str = "") -> dict:
    log = check_dir() / "rustdoc.log"
    rc = shell().tee(
        *make(worktree, build_dir, counting(make_flags), "rustdoc"), log=log
    )
    findings = rust_diagnostics(log.read_text(), worktree)
    return result("rustdoc", findings, log, failed=rc != 0)
