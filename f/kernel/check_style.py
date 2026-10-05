# SPDX-License-Identifier: copyleft-next-0.3.1
"""Run the kernel's patch style checker over the series.

Documentation/process/submit-checklist.rst: "Check for trivial violations
with the patch style checker prior to submission (`scripts/checkpatch.pl`).
You should be able to justify all violations that remain in your patch."
Patch mode is the checker's default and what the process documentation asks
for. `--terse --show-types` prints one `file:line: LEVEL:TYPE: message` line
per finding; `--strict` adds the CHECK level (Documentation/dev-tools/checkpatch.rst).
An ERROR fails the check; WARNING and CHECK are counted, for the author to
justify. The exit status is non-zero for any finding, so the verdict comes
from the log instead; a log with no `total:` summary line means the checker
never ran, which fails the check.

Equivalent bash, run inside the nixos-flake check-kernel devShell, from the
Worktree root:

    scripts/checkpatch.pl --terse --show-types [--strict] "$check_dir"/patches/0*.patch \\
        | tee "$check_dir/checkpatch.log"
"""

from __future__ import annotations

from f.kernel.checks import check_dir, diagnostics, result, shell


def main(worktree: str, patches: list[str], strict: bool = True) -> dict:
    log = check_dir() / "checkpatch.log"
    rc = shell().tee(
        "scripts/checkpatch.pl",
        "--terse",
        "--show-types",
        *(["--strict"] if strict else []),
        *patches,
        log=log,
        cwd=worktree,
    )
    text = log.read_text()
    ran = any(line.startswith("total:") for line in text.splitlines())
    return result(
        "checkpatch", diagnostics(text), log, strict=strict, failed=rc != 0 and not ran
    )
