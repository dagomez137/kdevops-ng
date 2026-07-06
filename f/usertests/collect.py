# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fold one usertests harness run into a verdict (pure, no guest contact).

Judges the run `f/usertests/wait` observed against the item's catalog entry;
the guest is never contacted, so a crashed guest still gets an honest verdict.
Output conventions are per harness and deliberately inconsistent upstream
(verified against the v7.2-rc1 tools/testing sources), so the rules compose:

* a crash or a timeout fails the item, whatever else looks plausible;
* the start job must have finished (`done`) with a clean exit: the template's
  `ExecStart` carries no `-` prefix, so exit codes are REAL, and an assert or
  sanitizer abort arrives as `result=failed` with `exec_status` populated
  (e.g. signal ABRT); that IS the test failure, so the reason carries the
  `exec_status`;
* global scans over the run's output: any `ERROR: AddressSanitizer`,
  `ERROR: LeakSanitizer` or `runtime error:` (UBSan) line fails the item, and
  so does any `assertion failed at` line, EXCEPT the catalog's expected
  noise: ida_check_bad_free deliberately frees unallocated ids between
  its 'vvv Ignore "not allocated" warnings' and '^^^ "not allocated" warnings
  over' markers), which is stripped before the scan;
* the catalog's per-entry evidence rule: a summary line whose counts must
  all pass and cover at least one test (`passed`/`total` equal, or vma's
  `run`/`passed`/`failed` with failed == 0; a missing summary fails, so a
  truncated capture can never pass), memblock's `--verbose` per-test lines
  (zero `: failed`, at least one `: passed`), radix-tree/main's
  `tests completed` sentinel, or, for the silent_ok harnesses (multiorder,
  scatterlist, the rbtree bench pair), the clean exit alone;
* vma's per-failure `Test <name> FAILED` lines become individual rows;
* radix-tree/main's logged `random seed %u` is extracted into the returned
  row (the reproducibility datum: rerun with `-s <seed>`).

Returns a scalar-topped dict so the run flow's per-item forloop renders a tidy
one-row-per-item table; the synthetic per-check rows and the output tail ride
under `detail`, consumed by `f/usertests/report`.
"""

from __future__ import annotations

import re

from f.common.remote import list_vms as _list_vms
from f.usertests.common import catalog_entry

_SANITIZER_MARKERS = (
    "ERROR: AddressSanitizer",
    "ERROR: LeakSanitizer",
    "runtime error:",
)
_ASSERT_MARKER = "assertion failed at"
_NOISE_OPEN = 'vvv Ignore "not allocated" warnings'
_NOISE_CLOSE = '^^^ "not allocated" warnings over'
_SEED_RE = re.compile(r"random seed (\d+)")
_VMA_FAILED_RE = re.compile(r"Test \w+ FAILED")
_TAIL_LINES = 40


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def _strip_expected_noise(lines: list[str]) -> list[str]:
    """The output minus idr-test's expected-warning block: everything between a
    line carrying the 'vvv Ignore "not allocated" warnings' marker and one
    carrying '^^^ "not allocated" warnings over' (markers included) is
    deliberate noise whose `assertion failed at` lines must not fail the run."""
    out: list[str] = []
    in_block = False
    for ln in lines:
        if _NOISE_OPEN in ln:
            in_block = True
            continue
        if _NOISE_CLOSE in ln:
            in_block = False
            continue
        if not in_block:
            out.append(ln)
    return out


def main(
    vm_name: str,
    item: str,
    kernel_version: str,
    output: str = "",
    result: str = "",
    exec_status: str = "",
    crashed: bool = False,
    timed_out: bool = False,
    runtime: float | None = None,
    started_realtime_ms: int | None = None,
    ended_realtime_ms: int | None = None,
) -> dict:
    entry = catalog_entry(item)
    # (check, message) pairs; any one fails the item and becomes a report row.
    findings: list[tuple[str, str]] = []

    if crashed:
        findings.append(("plumbing", "guest went down during the run"))
    if timed_out:
        findings.append(("plumbing", "run timed out (unit stopped by wait)"))
    if result != "done":
        findings.append(
            (
                "plumbing",
                f"start job ended {result!r}, not done "
                f"(exec_status={exec_status!r}: the harness's real exit; an "
                f"assert or sanitizer abort lands here as its signal)",
            )
        )
    elif str(exec_status) not in ("", "0"):
        findings.append(
            ("plumbing", f"harness exited nonzero (exec_status={exec_status!r})")
        )

    lines = (output or "").splitlines()
    scanned = _strip_expected_noise(lines)
    expected = entry["expected_assert_re"]
    if expected:
        exp_re = re.compile(expected)
        scanned = [ln for ln in scanned if not exp_re.search(ln)]
    for marker in _SANITIZER_MARKERS:
        hits = [ln for ln in scanned if marker in ln]
        if hits:
            findings.append(("sanitizer", hits[0].strip()))
    asserts = [ln for ln in scanned if _ASSERT_MARKER in ln]
    if asserts:
        findings.append(
            (
                "assertion",
                f"{len(asserts)} assertion line(s), first: {asserts[0].strip()}",
            )
        )
    if item == "vma/vma":
        for ln in lines:
            if _VMA_FAILED_RE.search(ln):
                findings.append(("vma test", ln.strip()))

    # Run evidence, per the catalog entry's policy.
    tests = passed = failed = 0
    evidence = ""
    if entry["summary_re"]:
        matches = list(re.finditer(entry["summary_re"], output or ""))
        if not matches:
            findings.append(
                (
                    "summary",
                    "no summary line in the run's output; a truncated capture "
                    "can never pass",
                )
            )
        else:
            groups = matches[-1].groupdict()
            if "failed" in groups:
                tests = int(groups["run"])
                passed = int(groups["passed"])
                failed = int(groups["failed"])
                if failed != 0:
                    findings.append(
                        ("summary", f"summary counts {failed} failed test(s)")
                    )
                if tests <= 0:
                    findings.append(("summary", "summary counts zero tests"))
            else:
                passed = int(groups["passed"])
                tests = int(groups["total"])
                failed = tests - passed
                if tests <= 0:
                    findings.append(("summary", "summary counts zero tests"))
                elif passed != tests:
                    findings.append(
                        ("summary", f"summary counts {passed} of {tests} passed")
                    )
            evidence = matches[-1].group(0)
    elif entry["count_lines"]:
        passed = sum(1 for ln in lines if re.search(entry["sentinel_re"], ln))
        failed = sum(1 for ln in lines if re.search(entry["fail_line_re"], ln))
        tests = passed + failed
        if failed:
            findings.append(("verbose counts", f"{failed} ': failed' line(s)"))
        if passed < 1:
            findings.append(
                (
                    "verbose counts",
                    "no ': passed' lines; the --verbose per-test output is "
                    "missing, so the run cannot be proven",
                )
            )
        evidence = f"{passed} per-test ': passed' line(s)"
    elif entry["sentinel_re"]:
        if re.search(entry["sentinel_re"], output or "", re.MULTILINE) is None:
            findings.append(
                (
                    "sentinel",
                    f"sentinel {entry['sentinel_re']!r} missing from the run's "
                    f"output; the suite cannot be proven to have run",
                )
            )
        else:
            evidence = "sentinel line present"
    else:
        # silent_ok: the clean exit (plus the global scans above) is the verdict.
        evidence = "clean exit (silent on pass)"

    seed_matches = _SEED_RE.findall(output or "")
    seed = int(seed_matches[-1]) if seed_matches else None
    if seed is not None:
        evidence = f"{evidence}; random seed {seed}" if evidence else str(seed)

    status = "failed" if findings else "passed"
    per_test = [
        {"harness": item, "check": check, "status": "failed", "message": message}
        for check, message in findings
    ]
    if not findings:
        per_test.append(
            {
                "harness": item,
                "check": entry["label"],
                "status": "passed",
                "message": evidence,
            }
        )

    reasons = [f"{check}: {message}" for check, message in findings]
    print(
        f"harness {item}: status={status} result={result!r} "
        f"exec_status={exec_status!r} crashed={crashed} timed_out={timed_out} "
        f"tests={tests} passed={passed} failed={failed} seed={seed}"
        + (f" reasons: {'; '.join(reasons)}" if reasons else ""),
        flush=True,
    )
    return {
        "item": item,
        "vm_name": vm_name,
        "kernel_version": kernel_version,
        "status": status,
        "result": result,
        "exec_status": exec_status,
        "crashed": crashed,
        "timed_out": timed_out,
        "runtime": runtime,
        "started_realtime_ms": started_realtime_ms,
        "ended_realtime_ms": ended_realtime_ms,
        "seed": seed,
        "tests": tests,
        "passed": passed,
        "failed": failed,
        "skipped": 0,
        "detail": {
            "per_test": per_test,
            "reasons": reasons,
            "output_tail": lines[-_TAIL_LINES:],
        },
    }
