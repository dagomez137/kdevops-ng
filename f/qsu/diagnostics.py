# SPDX-License-Identifier: copyleft-next-0.3.1
"""Parse sanitizer diagnostics from a guest's QEMU journal and rule on them.

Pure data module (a noun, not a step), imported by `f/qsu/collect_diagnostics`.
A QEMU built under a sanitizer writes its findings to the emulator's standard
error, which the guest's `qemu-system@<vm>.service` routes to the host journal.
This module turns those free-text lines into structured findings and a verdict;
the step does the journal read and hands the message text here.

Everything here is pure: no journal, no systemd, no environment. It exists so the
parse and verdict rules are tested against fixed strings (`tests/`) with no
instance, the same split every suite executor uses for its parser.

The load-bearing fact about the sanitizer runtimes: each source location is
reported once per process. A count of lines is therefore a count of distinct
locations, never a count of how often the code was reached, so the verdict keys
on the presence of a location and never infers severity or frequency from a
tally.
"""

from __future__ import annotations

import re

from f.qemu.sanitizers import SANITIZERS

# The sanitizer selections that name a build; `none` is not one. The one source
# for the set is the build-side table, so a new selection is detectable here for
# free.
_SELECTIONS = frozenset(SANITIZERS) - {"none"}

# UndefinedBehaviorSanitizer: `<file>:<line>:<col>: runtime error: <message>`. The
# file is whatever the compiler recorded, so it may be relative (`../hw/nvme/...`).
_UBSAN_RE = re.compile(
    r"^(?P<file>[^\s:][^:]*):(?P<line>\d+):(?P<col>\d+): "
    r"runtime error: (?P<message>.*)$"
)

# AddressSanitizer/ThreadSanitizer/LeakSanitizer announce with a header line
# `==<pid>==ERROR: <Name>Sanitizer: <kind> ...`; the source location follows in a
# `SUMMARY:` line. The header carries the kind, so it is the finding anchor.
_ASAN_RE = re.compile(
    r"==\d+==ERROR: (?P<name>Address|Thread|Leak)Sanitizer: (?P<kind>[\w-]+)"
)
_SUMMARY_RE = re.compile(
    r"SUMMARY: (?P<name>Address|Undefined|Thread|Leak)Sanitizer: "
    r"(?P<kind>[\w-]+)(?: (?P<file>[^\s:]+):(?P<line>\d+))?"
)

_RUNTIME_NAME = {
    "Address": "asan",
    "Thread": "tsan",
    "Leak": "lsan",
    "Undefined": "ubsan",
}

# A short category for the message, purely to make a report table legible; the
# `message` and `raw` fields carry the full text regardless.
_CATEGORY = (
    ("shift exponent", "shift"),
    ("integer overflow", "overflow"),
    ("misaligned", "alignment"),
    ("out of bounds", "bounds"),
    ("null pointer", "null-deref"),
    ("division by zero", "divide"),
    ("use-after-free", "use-after-free"),
    ("use-after-return", "use-after-return"),
    ("stack-overflow", "stack-overflow"),
    ("heap-buffer-overflow", "heap-overflow"),
    ("data race", "data-race"),
)


def _category(text: str) -> str:
    low = text.lower()
    for needle, label in _CATEGORY:
        if needle in low:
            return label
    return "runtime-error"


def parse(messages: list[str]) -> list[dict]:
    """Parse journal message lines into deduplicated sanitizer findings.

    `messages` is one string per journal record, in journal order. A finding is
    `{sanitizer, category, file, line, col, message, raw}`; `file`/`line`/`col` are
    `None` when the sanitizer did not report a location on that line. Findings are
    deduplicated by their location key, so a report that recurs across process
    restarts (each restart is a fresh process, so each re-reports its once) collapses
    to one entry.
    """
    findings: list[dict] = []
    seen: set[tuple] = set()
    for line in messages:
        finding = _parse_line(line.rstrip("\n"))
        if finding is None:
            continue
        key = (
            finding["sanitizer"],
            finding["file"],
            finding["line"],
            finding["col"],
            finding["category"],
        )
        if key in seen:
            continue
        seen.add(key)
        findings.append(finding)
    return findings


def _parse_line(line: str) -> dict | None:
    m = _UBSAN_RE.match(line)
    if m:
        return {
            "sanitizer": "ubsan",
            "category": _category(m["message"]),
            "file": m["file"],
            "line": int(m["line"]),
            "col": int(m["col"]),
            "message": m["message"],
            "raw": line,
        }
    m = _ASAN_RE.search(line)
    if m:
        return {
            "sanitizer": _RUNTIME_NAME[m["name"]],
            "category": m["kind"],
            "file": None,
            "line": None,
            "col": None,
            "message": line.split("ERROR: ", 1)[-1],
            "raw": line,
        }
    m = _SUMMARY_RE.search(line)
    if m:
        return {
            "sanitizer": _RUNTIME_NAME[m["name"]],
            "category": m["kind"],
            "file": m["file"],
            "line": int(m["line"]) if m["line"] else None,
            "col": None,
            "message": line.split("SUMMARY: ", 1)[-1],
            "raw": line,
        }
    return None


def locations(findings: list[dict]) -> list[str]:
    """The distinct `file:line` sites among findings, in first-seen order."""
    out: list[str] = []
    for f in findings:
        if f["file"] and f["line"] is not None:
            site = f"{f['file']}:{f['line']}"
            if site not in out:
                out.append(site)
    return out


def verdict(findings: list[dict], sanitizer: str) -> dict:
    """Rule on a run's findings given the build's sanitizer selection.

    `diagnostics` when the emulator tripped its sanitizer (unambiguous, whatever the
    selection). Otherwise `clean` for a sanitized build that stayed quiet, and
    `not_sanitized` for a stock build where a diagnostic was never possible, so a
    quiet run is not misread as a passed check. `ok` is True only for a run that had
    nothing to report.
    """
    name = sanitizer or "none"
    if findings:
        status = "diagnostics"
    elif name in _SELECTIONS:
        status = "clean"
    else:
        status = "not_sanitized"
    return {
        "status": status,
        "ok": status != "diagnostics",
        "sanitizer": name,
        "count": len(findings),
        "locations": locations(findings),
        "findings": findings,
    }


def sanitizer_from_store_name(store_name: str) -> str:
    """The sanitizer a QEMU store name encodes, or `none`; best-effort.

    A build's install prefix and store key end `...-<sanitizer>-<identity>`, where
    identity is 12 hex and the sanitizer segment is present only for a sanitized
    build. This reads that positional segment, so a label that merely contains a
    sanitizer word (the `ubsan-test` branch, say) is not mistaken for one: only the
    token immediately before the identity counts. It stays a fallback for a
    standalone call; a flow passes the selection from the build manifest, where it is
    unambiguous, because a label whose own last token equals a sanitizer name would
    still fool the positional read.
    """
    m = re.search(r"-(?P<seg>[a-z+]+)-[0-9a-f]{12}$", store_name)
    if m and m["seg"] in _SELECTIONS:
        return m["seg"]
    return "none"
