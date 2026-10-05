# SPDX-License-Identifier: copyleft-next-0.3.1
"""Check that the series keeps the UAPI headers backwards compatible.

Documentation/dev-tools/checkuapi.rst: "The UAPI checker
(`scripts/check-uapi.sh`) is a shell script which checks UAPI header files for
userspace backwards-compatibility across the git tree." It installs the
headers at the series' base and at HEAD and compares each with `abidiff`. It
runs only when the series touches a UAPI header, whatever the scope. Exit 1
(an ABI difference) fails the check; exit 2 (a missing prerequisite) fails
the step, since nothing was compared, and so does exit 1 without the script's
own report, which is the devShell failing before the script ran. The script runs a libdw version probe
through `ldconfig` when `CC` is `clang`, which cannot see a Nix store library,
and the devShell exports `CC=clang`; the headers compile the same with GCC,
so the command runs under `env CC=gcc` (the devShell's own setup would undo
the variable set from outside it).

Equivalent bash, run inside the nixos-flake check-kernel devShell, from the
Worktree root:

    env CC=gcc scripts/check-uapi.sh -b HEAD -p "$base_commit" -l "$check_dir/check-uapi.errors" \\
        | tee "$check_dir/check-uapi.log"
"""

from __future__ import annotations

import re

from f.kernel.checks import check_dir, result, shell

_UAPI = re.compile(r"^(?:include/uapi/|arch/[^/]+/include/uapi/)")
_ABI_DIFFERENCE, _PREREQUISITE = 1, 2
# The script's own summary of an ABI difference.
_REPORT = "appear _not_ to be backwards compatible"


def main(worktree: str, files: list[str], base_commit: str) -> dict:
    headers = [f for f in files if _UAPI.match(f) and f.endswith(".h")]
    if not headers:
        print("the series changes no UAPI header", flush=True)
        return result("check-uapi", [], None, headers=[])
    errors = check_dir() / "check-uapi.errors"
    log = check_dir() / "check-uapi.log"
    rc = shell().tee(
        "env",
        "CC=gcc",
        "scripts/check-uapi.sh",
        "-b",
        "HEAD",
        "-p",
        base_commit,
        "-l",
        str(errors),
        log=log,
        cwd=worktree,
    )
    if rc == _PREREQUISITE:
        raise RuntimeError(f"check-uapi.sh: a prerequisite is missing, see {log}")
    if rc == _ABI_DIFFERENCE and _REPORT not in log.read_text():
        raise RuntimeError(f"check-uapi.sh did not run, see {log}")
    findings = []
    if rc == _ABI_DIFFERENCE:
        findings.append(
            {
                "file": str(errors),
                "line": None,
                "level": "error",
                "message": "a UAPI header changed incompatibly",
            }
        )
    return result("check-uapi", findings, log, headers=headers, failed=rc != 0)
