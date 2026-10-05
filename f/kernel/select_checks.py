# SPDX-License-Identifier: copyleft-next-0.3.1
"""Decide which checks run, from the picked set and the configured Build.

`CHECKS` is the one list of checks the flow offers, by the upstream tool's
own name, each mapped to the step that runs it. A pick of nothing (`None`)
takes `DEFAULT`, the checks fast enough to run on every series; Coccinelle,
the Clang analyzers and the documentation build are opt-in. The Rust checks
run only on a Build configured with `CONFIG_RUST=y`, and the Clang analyzers
only on one that uses Clang; a picked check the Build cannot take is skipped
with the reason, so the report says why it is missing.
"""

from __future__ import annotations

from f.kernel.checks import SCOPES, config_has

# Check name -> step id, in report order.
CHECKS = {
    "W=1": "check_warnings",
    "checkpatch": "check_style",
    "sparse": "check_sparse",
    "checkstack": "check_stack",
    "kernel-doc": "check_kerneldoc",
    "check-uapi": "check_uapi",
    "coccicheck": "check_cocci",
    "clang-tidy": "check_clang",
    "clang-analyzer": "check_clang",
    "htmldocs": "build_htmldocs",
    "rustavailable": "check_rustavailable",
    "rustfmt": "check_rustfmt",
    "clippy": "check_clippy",
    "rustdoc": "build_rustdoc",
    "rusttest": "run_rusttest",
    "get_maintainer": "find_maintainers",
}
DEFAULT = [
    "W=1",
    "checkpatch",
    "sparse",
    "checkstack",
    "kernel-doc",
    "check-uapi",
    "rustavailable",
    "rustfmt",
    "clippy",
    "rustdoc",
    "rusttest",
    "get_maintainer",
]
RUST = ("rustavailable", "rustfmt", "clippy", "rustdoc", "rusttest")
CLANG = ("clang-tidy", "clang-analyzer")


def main(
    build_dir: str,
    checks: list[str] | None = None,
    scope: str = "series",
) -> dict:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}, got {scope!r}")
    picked = DEFAULT if checks is None else list(checks)
    unknown = sorted(set(picked) - set(CHECKS))
    if unknown:
        raise ValueError(f"unknown checks: {', '.join(unknown)}")

    rust = config_has(build_dir, "CONFIG_RUST")
    clang = config_has(build_dir, "CONFIG_CC_IS_CLANG")
    skipped = {}
    for name in picked:
        if name in RUST and not rust:
            skipped[name] = "CONFIG_RUST is not set"
        elif name in CLANG and not clang:
            skipped[name] = "the Build does not use Clang"
    selected = [n for n in CHECKS if n in picked and n not in skipped]
    run = {step: False for step in CHECKS.values()}
    for name in selected:
        run[CHECKS[name]] = True

    print(f"checks: {', '.join(selected) or 'none'}", flush=True)
    for name, why in skipped.items():
        print(f"skipping {name}: {why}", flush=True)
    return {
        "scope": scope,
        "checks": selected,
        "skipped": skipped,
        "run": run,
        "clang_tools": [n for n in CLANG if n in selected],
        "rust": rust,
    }
