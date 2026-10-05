# SPDX-License-Identifier: copyleft-next-0.3.1
"""Build the HTML documentation and count Sphinx's findings.

Documentation/process/submit-checklist.rst: "Any Documentation/ changes build
successfully without new warnings/errors. Use `make htmldocs` or `make
pdfdocs` to check the build and fix any issues." `scope=series` builds only
the Documentation/ subdirectories the series touches, through `SPHINXDIRS`
(Documentation/doc-guide/sphinx.rst), and skips the build when it touches
none; `scope=worktree` builds everything. A failed build or a Sphinx ERROR fails
the check; WARNINGs are counted.

Equivalent bash, run inside the nixos-flake check-kernel devShell:

    make --directory="$worktree" O="$build_dir" $make_flags \\
        [SPHINXDIRS="core-api"] htmldocs | tee "$check_dir/htmldocs.log"
"""

from __future__ import annotations

from pathlib import Path

from f.kernel.checks import check_dir, diagnostics, in_files, make, result, shell


def _sphinx_dirs(worktree: str, files: list[str]) -> list[str]:
    dirs = {
        f.split("/")[1]
        for f in files
        if f.startswith("Documentation/") and f.count("/") >= 2
    }
    return sorted(d for d in dirs if (Path(worktree) / "Documentation" / d).is_dir())


def main(
    worktree: str,
    build_dir: str,
    files: list[str],
    scope: str = "series",
    make_flags: str = "",
) -> dict:
    goals = ["htmldocs"]
    dirs: list[str] = []
    if scope == "series":
        dirs = _sphinx_dirs(worktree, files)
        if not dirs:
            print("the series changes no Documentation/ subdirectory", flush=True)
            return result("htmldocs", [], None, scope=scope, sphinx_dirs=[])
        goals.insert(0, f"SPHINXDIRS={' '.join(dirs)}")
    log = check_dir() / "htmldocs.log"
    rc = shell().tee(*make(worktree, build_dir, make_flags, *goals), log=log)
    findings = diagnostics(log.read_text(), worktree)
    if scope == "series":
        findings = in_files(findings, files)
    return result(
        "htmldocs", findings, log, scope=scope, sphinx_dirs=dirs, failed=rc != 0
    )
