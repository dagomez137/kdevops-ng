# SPDX-License-Identifier: copyleft-next-0.3.1
"""Shared helpers for the `f/kernel` check steps (not a runnable step).

Imported with:  from f.kernel.checks import ...

Every check reads the same series, the files the commits between the merge
base and HEAD add or modify, writes its tool's output to a log in the Worker
sandbox's `check/` directory, and counts the findings from that log. One rule
turns the counts into a status (`status`): an error fails the check, and a
warning is counted for the author to justify, as
Documentation/process/submitting-patches.rst asks of `checkpatch.pl`'s output.

`scope` picks what a checker that can read the whole Worktree looks at:
`series` narrows it to the series' own files, `worktree` checks every file, as
a maintainer does before pushing.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from f.common.devshell import DevShell, Git

SCOPES = ("series", "worktree")

# Directories kbuild does not build into kernel objects.
NOT_KERNEL_OBJECTS = ("tools/", "scripts/", "samples/", "Documentation/")

# The most findings a step returns; the log keeps all of them.
MAX_FINDINGS = 100

# file:line[:col[-col]]: level: message, the form GCC, Clang, Sparse,
# kernel-doc, Coccinelle, Sphinx and `checkpatch.pl --terse` share.
_DIAGNOSTIC = re.compile(
    r"^(?P<file>[^\s:]+):(?:(?P<line>\d+):)?(?:\d+(?:-\d+)?:)?\s*(?:fatal )?"
    r"(?P<level>warning|error|WARNING|ERROR|CHECK|CRITICAL):\s*(?P<message>.*)$"
)
# kernel-doc since v7.0: `Warning: file:line message`.
_KERNELDOC = re.compile(
    r"^(?P<level>Warning|Error): (?P<file>[^\s:]+):(?P<line>\d+) (?P<message>.*)$"
)
# `rustc` and Clippy put the location on a later `-->` line.
_RUST_HEADER = re.compile(
    r"^(?P<level>warning|error)(?:\[[\w:-]+\])?: (?P<message>.*)$"
)
_RUST_SUMMARY = re.compile(
    r"^(?:warning: \d+ warnings? emitted|error: aborting due to|"
    r"warning: .* generated \d+ warnings?|error: could not compile)"
)
_RUST_LOCATION = re.compile(r"^\s*--> (?P<file>[^:]+):(?P<line>\d+):\d+")
_RUSTFMT_DIFF = re.compile(
    r"^Diff in (?P<file>\S+?)(?::(?P<line>\d+):| at line (?P<at>\d+):)"
)
_RUSTTEST_RESULT = re.compile(
    r"^test result: \w+\. (?P<passed>\d+) passed; (?P<failed>\d+) failed"
)
_CHECKSTACK = re.compile(
    r"^(?:0x[0-9a-f]+\s+)?(?P<function>\S+)\s+\[(?P<object>[^\]]+)\]:\s+(?P<bytes>\d+)$"
)

_LEVELS = {
    "error": "error",
    "ERROR": "error",
    "CRITICAL": "error",
    "warning": "warning",
    "WARNING": "warning",
    "CHECK": "check",
    "Warning": "warning",
    "Error": "error",
}


def main():
    """This module is a library imported by the check steps, not a runnable step."""
    return "f/kernel/checks: shared series, log and finding helpers"


def check_dir() -> Path:
    """The Worker sandbox's `check/`: the series' patches and every check's log."""
    return Path(os.environ["WORKERS_DIR"]) / os.environ["WORKER_INDEX"] / "check"


def shell() -> DevShell:
    return DevShell(Path(os.environ["WORKERS_DIR"]), shell="check-kernel")


def make(worktree: str, build_dir: str, make_flags: str, *goals: str) -> list[str]:
    return [
        "make",
        f"--directory={worktree}",
        f"O={build_dir}",
        f"--jobs={len(os.sched_getaffinity(0))}",
        *shlex.split(make_flags),
        *goals,
    ]


def series_files(worktree: str, base: str) -> list[str]:
    """Files the commits since `base` add or modify, relative to the Worktree."""
    out = Git().capture(
        "-C", worktree, "diff", "--name-only", "--diff-filter=AM", base, "HEAD"
    )
    return sorted(line for line in out.splitlines() if line)


def kernel_objects(files: list[str], build_dir: str) -> list[str]:
    """The kbuild object of each C file the Build compiled, for `make` goals.

    A file the configuration leaves out has no `.<name>.o.cmd` in the Build,
    and naming its object would compile it against a config it was never
    meant for.
    """
    objects = []
    for f in files:
        if not f.endswith(".c") or f.startswith(NOT_KERNEL_OBJECTS):
            continue
        obj = Path(f.removesuffix(".c") + ".o")
        if (Path(build_dir) / obj.parent / f".{obj.name}.cmd").is_file():
            objects.append(str(obj))
        else:
            print(f"skipping {f}: the Build does not compile it", flush=True)
    return objects


def counting(make_flags: str) -> str:
    """`make_flags` with warnings kept as warnings, so a check can count them.

    `CONFIG_WERROR` adds `-Werror` and `rustc`'s `-Dwarnings`, and the first
    warning would stop the `make` the check reads. `KCFLAGS=-Wno-error` and
    `KRUSTFLAGS=--cap-lints=warn` come later on the compiler command line and
    win, merged into any `KCFLAGS`/`KRUSTFLAGS` already set. Only a check's own
    `make` takes them: they are not the Build's flags, so its identity is unchanged.
    """
    tokens = shlex.split(make_flags)
    for var, flag in (("KCFLAGS", "-Wno-error"), ("KRUSTFLAGS", "--cap-lints=warn")):
        idx = next((i for i, t in enumerate(tokens) if t.startswith(f"{var}=")), None)
        if idx is None:
            tokens.append(f"{var}={flag}")
        else:
            value = tokens[idx].split("=", 1)[1]
            tokens[idx] = f"{var}={f'{value} {flag}'.strip()}"
    return shlex.join(tokens)


def config_has(build_dir: str, symbol: str) -> bool:
    config = Path(build_dir) / ".config"
    return config.is_file() and f"{symbol}=y\n" in config.read_text()


def worktree_path(path: str, worktree: str = "") -> str:
    """A diagnostic's path relative to the Worktree root.

    A Build outside the Worktree reports sources as `../lib/xarray.c` or by
    absolute path; both name the same Worktree file.
    """
    if worktree and path.startswith(worktree.rstrip("/") + "/"):
        path = path[len(worktree.rstrip("/")) + 1 :]
    while path.startswith(("../", "./")):
        path = path.split("/", 1)[1]
    return path


def diagnostics(text: str, worktree: str = "") -> list[dict]:
    """Findings in the shared `file:line: level:` form, each reported once.

    A tool that reports a finding twice, as kernel-doc does for a function
    both declared and defined, still counts it once.
    """
    found, seen = [], set()
    for line in text.splitlines():
        m = _DIAGNOSTIC.match(line) or _KERNELDOC.match(line)
        if not m:
            continue
        finding = {
            "file": worktree_path(m["file"], worktree),
            "line": int(m["line"]) if m["line"] else None,
            "level": _LEVELS[m["level"]],
            "message": m["message"].strip(),
        }
        key = tuple(finding.values())
        if key not in seen:
            seen.add(key)
            found.append(finding)
    return found


def rust_diagnostics(text: str, worktree: str = "") -> list[dict]:
    """`rustc`, Clippy and `rustdoc` findings: a level line, then its `-->`."""
    found: list[dict] = []
    pending = None
    for line in text.splitlines():
        if _RUST_SUMMARY.match(line):
            pending = None
            continue
        header = _RUST_HEADER.match(line)
        if header:
            pending = {
                "file": "",
                "line": None,
                "level": header["level"],
                "message": header["message"].strip(),
            }
            found.append(pending)
            continue
        location = _RUST_LOCATION.match(line)
        if location and pending is not None and not pending["file"]:
            pending["file"] = worktree_path(location["file"], worktree)
            pending["line"] = int(location["line"])
    return found


def rustfmt_diffs(text: str, worktree: str = "") -> list[dict]:
    """One finding per hunk `rustfmt --check` would rewrite."""
    found = []
    for line in text.splitlines():
        m = _RUSTFMT_DIFF.match(line)
        if m:
            found.append(
                {
                    "file": worktree_path(m["file"], worktree),
                    "line": int(m["line"] or m["at"]),
                    "level": "error",
                    "message": "not formatted as rustfmt would",
                }
            )
    return found


def rusttest_totals(text: str) -> dict:
    passed = failed = 0
    for line in text.splitlines():
        m = _RUSTTEST_RESULT.match(line)
        if m:
            passed += int(m["passed"])
            failed += int(m["failed"])
    return {"passed": passed, "failed": failed}


def stack_frames(text: str) -> list[dict]:
    """`make checkstack` lines: function, object, frame size in bytes."""
    return [
        {"function": m["function"], "object": m["object"], "bytes": int(m["bytes"])}
        for m in map(_CHECKSTACK.match, text.splitlines())
        if m
    ]


def in_files(findings: list[dict], files: list[str]) -> list[dict]:
    wanted = set(files)
    return [f for f in findings if f["file"] in wanted]


def counts(findings: list[dict]) -> dict:
    return {
        "errors": sum(f["level"] == "error" for f in findings),
        "warnings": sum(f["level"] == "warning" for f in findings),
        "checks": sum(f["level"] == "check" for f in findings),
    }


def status(errors: int, failed: bool = False) -> str:
    return "failed" if errors or failed else "passed"


def result(check: str, findings: list[dict], log: Path | None, **extra) -> dict:
    """A check step's return: its counts and status, then the findings."""
    tally = counts(findings)
    verdict = status(tally["errors"], extra.pop("failed", False))
    print(
        f"{check}: {verdict}, {tally['errors']} errors, {tally['warnings']} warnings"
        + (f", {tally['checks']} checks" if tally["checks"] else ""),
        flush=True,
    )
    return {
        "check": check,
        "status": verdict,
        **tally,
        "log": str(log) if log else None,
        **extra,
        "findings": findings[:MAX_FINDINGS],
    }


def outcome(step_result) -> str:
    """A check step's status as the flow saw it.

    A step that did not run leaves no result; one that raised under
    `continue_on_error` leaves Windmill's error object, which fails the run
    like a failed check does.
    """
    if not step_result:
        return "not run"
    if "error" in step_result and "status" not in step_result:
        return "error"
    return step_result.get("status", "error")
