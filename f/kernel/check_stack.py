# SPDX-License-Identifier: copyleft-next-0.3.1
"""List the largest stack frames in the built kernel and its modules.

Documentation/process/submit-checklist.rst: "Use `make checkstack` and fix any
problems that it finds. Note that `checkstack` does not point out problems
explicitly, but any one function that uses more than 512 bytes on the stack is
a candidate for change." So this check never fails. It reads `vmlinux` and
every `.ko`, whatever the scope, and returns the largest frames. A frame over
512 bytes is counted as a warning when its function is one the series defines
(`scope=series`, a definition line the patches add), or any such frame
(`scope=worktree`).

Equivalent bash, run inside the nixos-flake check-kernel devShell:

    make --directory="$worktree" O="$build_dir" $make_flags checkstack \\
        | tee "$check_dir/checkstack.log"
"""

from __future__ import annotations

import re
from pathlib import Path

from f.kernel.checks import check_dir, make, result, shell, stack_frames

# The size submit-checklist.rst names.
_CANDIDATE_BYTES = 512
# An added C function definition line: return type, name, open parenthesis.
_DEFINITION = re.compile(r"^\+(?:[A-Za-z_][\w\s\*]*?[\s\*])(\w+)\(", re.MULTILINE)


def _defined(patches: list[str]) -> set[str]:
    return {name for p in patches for name in _DEFINITION.findall(Path(p).read_text())}


def main(
    worktree: str,
    build_dir: str,
    patches: list[str] | None = None,
    scope: str = "series",
    make_flags: str = "",
) -> dict:
    log = check_dir() / "checkstack.log"
    rc = shell().tee(*make(worktree, build_dir, make_flags, "checkstack"), log=log)
    frames = stack_frames(log.read_text())
    large = [f for f in frames if f["bytes"] > _CANDIDATE_BYTES]
    if scope == "series":
        defined = _defined(patches or [])
        large = [f for f in large if f["function"] in defined]
    findings = [
        {
            "file": f["object"],
            "line": None,
            "level": "warning",
            "message": f"{f['function']} uses {f['bytes']} bytes of stack",
        }
        for f in large
    ]
    return result(
        "checkstack", findings, log, scope=scope, frames=frames[:50], failed=rc != 0
    )
