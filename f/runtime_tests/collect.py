# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fold one runtime-test module's run into a verdict (pure, no guest contact).

Judges the module load `f/runtime_tests/wait` observed against the module's
catalog entry; the guest is never contacted, so a crashed guest still gets an
honest verdict. Exit conventions are per module and deliberately inconsistent
upstream, so the rules compose, each one a former-bug-class invariant:

* a crash or a timeout fails the item, whatever else looks plausible;
* the start job must have finished (`done`); a `skipped` outcome is the
  unit's `ConditionKernelModuleLoaded` refusing an already-loaded module, a
  failed run identity, never a pass;
* the post-run load state must match the catalog's `loaded_on_pass`: the
  exit-honest class stays loaded on a pass, so a missing module means its
  init failed; the auto-unload class must be gone, so a still-loaded module
  means init returned 0 where it must not, which for test_ida IS the test
  failure (it returns -EINVAL and unloads on PASS, 0 and stays loaded on
  FAIL). The load state stands in for the exit status, which is structurally
  unobservable here: modprobe@'s `-` prefixed `ExecStart` makes systemd
  treat any exit as expected, and expected process exits are logged only at
  debug level, which PID1 at its default log level never journals (live-run
  confirmed: both classes return `done` with an empty `exec_status`). The
  `exec_status` input is kept in the returned row for forensics only; it is
  not a rule input;
* any `WARNING:`/`BUG:`/`Call Trace:` line in the cursor-scoped kmsg fails
  the item regardless of the rest: for the -EAGAIN class that is the only
  failure channel (skipped only for test_ida, whose passing run fires
  deliberate ida_free warnings);
* a module-specific failure regex (test_vmalloc's per-worker `Summary: ...
  failed: N` lines) fails on match;
* run evidence is required: printed pass counts (which must parse, cover at
  least one test, and all pass; a truncated kmsg can never pass) or the
  module's sentinel line proving the suite ran. No evidence is a failure for
  a test module and `notrun` for a stress/benchmark one.

Returns a scalar-topped dict so the run flow's per-item forloop renders a tidy
one-row-per-item table; a synthetic per-module row and the kmsg tail ride
under `detail`, consumed by `f/runtime_tests/report`.
"""

from __future__ import annotations

import re

from f.common.remote import list_vms as _list_vms
from f.runtime_tests.common import catalog_entry

_SPLAT_MARKERS = ("WARNING:", "BUG:", "Call Trace:")
_TAIL_LINES = 40


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def _summary_counts(summary_re: str, kmsg: str) -> tuple[int, int] | None:
    """The (passed, total) of the LAST summary match in the run's kmsg; a
    missing named group defaults to the other (some modules print only one
    count); None when no line matches."""
    matches = list(re.finditer(summary_re, kmsg))
    if not matches:
        return None
    groups = matches[-1].groupdict()
    passed = groups.get("passed")
    total = groups.get("total")
    passed = int(passed if passed is not None else total or 0)
    total = int(total if total is not None else passed)
    return passed, total


def main(
    vm_name: str,
    module: str,
    kernel_version: str,
    kmsg: str = "",
    result: str = "",
    loaded: bool = False,
    exec_status: str = "",
    crashed: bool = False,
    timed_out: bool = False,
    runtime: float | None = None,
    started_realtime_ms: int | None = None,
    ended_realtime_ms: int | None = None,
) -> dict:
    entry = catalog_entry(module)
    reasons: list[str] = []

    if crashed:
        reasons.append("guest went down during the run")
    if timed_out:
        reasons.append("run timed out (unit stopped by wait)")
    if result == "skipped":
        reasons.append(
            "start job skipped: module already loaded "
            "(ConditionKernelModuleLoaded), nothing ran"
        )
    elif result != "done":
        reasons.append(f"start job ended {result!r}, not done")

    if entry["loaded_on_pass"] and not loaded:
        reasons.append(
            "module not loaded after the run: its init failed (the errno "
            "itself is unobservable through modprobe@'s ignored ExecStart)"
        )
    if not entry["loaded_on_pass"] and loaded:
        reasons.append(
            "module still loaded: init returned 0 where it must not (for "
            "test_ida that IS the test failure; for the auto-unload class it "
            "means the tests did not run)"
        )

    kmsg_lines = (kmsg or "").splitlines()
    if entry["scan_kmsg"]:
        splats = [ln for ln in kmsg_lines if any(m in ln for m in _SPLAT_MARKERS)]
        if splats:
            reasons.append(f"kernel splat in kmsg: {splats[0].strip()}")
    if entry["fail_re"]:
        hits = [ln for ln in kmsg_lines if re.search(entry["fail_re"], ln)]
        if hits:
            reasons.append(f"failure line in kmsg: {hits[0].strip()}")

    # Run evidence: summary counts, or the sentinel proving the suite ran.
    tests = passed = failed = 0
    evidence_missing = False
    if entry["summary_re"]:
        counts = _summary_counts(entry["summary_re"], kmsg or "")
        if counts is None:
            evidence_missing = True
        else:
            passed, tests = counts
            failed = tests - passed
            if tests <= 0:
                reasons.append("summary line counts zero tests")
            elif passed != tests:
                reasons.append(f"summary counts {passed} of {tests} tests passed")
    elif entry["sentinel_re"]:
        evidence_missing = re.search(entry["sentinel_re"], kmsg or "") is None

    if evidence_missing and entry["kind"] == "test":
        reasons.append(
            "no run evidence in kmsg (summary counts or sentinel line); "
            "the suite cannot be proven to have run"
        )

    if reasons:
        status = "failed"
    elif evidence_missing:
        # A stress/benchmark run that met its load-state expectation but left
        # no output proved nothing ran; that is not a pass.
        status = "notrun"
    else:
        status = "passed"

    message = "; ".join(reasons)
    print(
        f"module {module}: status={status} "
        f"loaded_on_pass={entry['loaded_on_pass']} result={result!r} "
        f"loaded={loaded} crashed={crashed} timed_out={timed_out} "
        f"tests={tests} passed={passed} failed={failed}"
        + (f" reasons: {message}" if message else ""),
        flush=True,
    )
    return {
        "item": module,
        "vm_name": vm_name,
        "kernel_version": kernel_version,
        "status": status,
        "result": result,
        "loaded": loaded,
        "exec_status": exec_status,
        "crashed": crashed,
        "timed_out": timed_out,
        "runtime": runtime,
        "started_realtime_ms": started_realtime_ms,
        "ended_realtime_ms": ended_realtime_ms,
        "tests": tests,
        "passed": passed,
        "failed": failed,
        "skipped": 0,
        "detail": {
            "per_test": [
                {
                    "module": module,
                    "test": entry["label"],
                    "status": status,
                    "message": message,
                }
            ],
            "reasons": reasons,
            "kmsg_tail": kmsg_lines[-_TAIL_LINES:],
        },
    }
