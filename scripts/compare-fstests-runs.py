#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Compare two fstests runs from their raw results directories, without
# bundles, an instance, or a running flow. The comparison semantics are
# the reference model for the results-bundle plan
# (notes/plans/nix-store-test-results.md): scope to the requested-set
# intersection, keep truncation distinct from notrun, lead with identity
# warnings, and report out-of-scope tests as set differences instead of
# dropping them.
#
#     python3 scripts/compare-fstests-runs.py \
#         --baseline <vm>/<kver>/results --candidate <vm>/<kver>/results \
#         [--section xfs_full_bs16k_ss16k] \
#         [--baseline-attempt 0001] [--candidate-attempt 0001] \
#         [--dump-manifests <dir>]
#
# An attempt is a rotated index ("0001", pairing result.0001.xml with
# check.0001.log) or "current" (the default): the section's unrotated
# result.xml plus the last summary block of its check.log. That block
# belongs to the current attempt when its test set equals the xunit's
# (a finished run) or strictly contains it while marked Interrupted!
# (the in-flight test is summary-only). Anything else, such as a run
# still writing its xunit, discards the block and degrades to
# "requested set unknown" rather than lying. The acceptance is by set
# shape, not timestamps, so a live run that is a strict subset of a
# previous INTERRUPTED block can in principle be misattributed; the
# bundle plan closes that hole by validating against the run window.

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCHEMA = 1

# Device names differ across guests (nvme4n1 vs nvme2n1) and pollute
# notrun-reason comparison; normalize at compare time, never in the
# stored evidence.
_DEV_RE = re.compile(r"(?:/dev/)?nvme\d+n\d+(?:p\d+)?")


def _last_summary_block(text: str) -> dict:
    """The last Ran/Not run/Failures block of an appending check.log."""
    ran: list[str] = []
    notrun: list[str] = []
    failures: list[str] = []
    interrupted = False
    for line in text.splitlines():
        if line.startswith("Ran: "):
            ran = line.split()[1:]
            notrun, failures, interrupted = [], [], False
        elif line.startswith("Not run: "):
            notrun = line.split()[2:]
        elif line.startswith("Failures: "):
            failures = line.split()[1:]
        elif line.startswith("Interrupted!"):
            interrupted = True
    return {
        "ran": ran,
        "notrun": notrun,
        "failures": failures,
        "interrupted": interrupted,
    }


def _parse_xunit(path: Path) -> tuple[dict, dict]:
    root = ET.parse(path).getroot()
    window = {
        "start": root.get("start_timestamp"),
        "end": root.get("timestamp"),
        "hostname": root.get("hostname"),
        "time_s": int(float(root.get("time") or 0)),
    }
    tests: dict[str, dict] = {}
    for case in root.iter("testcase"):
        name = case.get("name") or ""
        rec: dict = {"status": "passed"}
        time_s = case.get("time")
        if time_s is not None:
            rec["time_s"] = int(float(time_s))
        failure = case.find("failure")
        skipped = case.find("skipped")
        if failure is not None:
            rec["status"] = "failed"
            rec["message"] = (failure.get("message") or "")[:200]
        elif skipped is not None:
            rec["status"] = "notrun"
            rec["reason"] = (skipped.get("message") or "")[:200]
        tests[name] = rec
    return tests, window


def _accept_summary(summary: dict, tests: dict) -> bool:
    """Whether the log block describes the xunit's attempt."""
    block = set(summary["ran"]) | set(summary["notrun"])
    if block == set(tests):
        return True
    # An interrupted attempt lists the in-flight test in Ran before the
    # xunit report is cut, so the block strictly contains the xml.
    return summary["interrupted"] and set(tests) < block


def _build_manifest(results_dir: Path, section: str, attempt: str) -> dict:
    sect = results_dir / section
    if attempt == "current":
        xml, log = sect / "result.xml", sect / "check.log"
    else:
        xml = sect / f"result.{attempt}.xml"
        log = sect / f"check.{attempt}.log"
    if not xml.is_file():
        sys.exit(f"error: {xml} does not exist")

    tests, window = _parse_xunit(xml)
    summary = _last_summary_block(log.read_text()) if log.is_file() else None
    if summary is not None and not _accept_summary(summary, tests):
        print(f"note: last {log} block does not match the xunit; ignoring")
        summary = None

    interrupted = bool(summary and summary["interrupted"])
    # An interrupted attempt's artifacts cannot name the tail it never
    # reached, so its requested set stays unknown on purpose.
    requested = None
    if summary and not interrupted:
        requested = sorted(set(summary["ran"]) | set(summary["notrun"]))

    geometry = None
    geo = results_dir.parent.parent / f"{section}.geometry.json"
    if geo.is_file():
        geometry = json.loads(geo.read_text())

    return {
        "schema": SCHEMA,
        "suite": "fstests",
        "vm": results_dir.parent.parent.name,
        "kernel": {"release": results_dir.parent.name},
        "corpus": {"store_path": None, "locked_rev": None},
        "window": window,
        "section": {"name": section, "geometry": geometry},
        "run": {
            "requested": requested,
            "truncated": interrupted,
            "counts": {
                "recorded": len(tests),
                "passed": sum(1 for r in tests.values() if r["status"] == "passed"),
                "failed": sum(1 for r in tests.values() if r["status"] == "failed"),
                "notrun": sum(1 for r in tests.values() if r["status"] == "notrun"),
            },
        },
        "tests": tests,
        "retro": True,
    }


def _detect_section(results_dir: Path, wanted: str) -> str:
    if wanted:
        return wanted
    sections = sorted(
        d.name
        for d in results_dir.iterdir()
        if d.is_dir() and (d / "result.xml").is_file()
    )
    if not sections:
        sys.exit(f"error: no section directory with a result.xml under {results_dir}")
    if len(sections) > 1:
        sys.exit(
            f"error: {results_dir} holds sections {sections}; pick one with --section"
        )
    return sections[0]


def _status(manifest: dict, test: str) -> str:
    rec = manifest["tests"].get(test)
    return rec["status"] if rec else "notrecorded"


def _why(manifest: dict, test: str) -> str:
    rec = manifest["tests"].get(test, {})
    return rec.get("reason") or rec.get("message") or ""


def _banner(base: dict, cand: dict) -> None:
    for side, m in (("baseline ", base), ("candidate", cand)):
        corpus = (
            m["corpus"]["locked_rev"]
            or m["corpus"]["store_path"]
            or "UNKNOWN (raw results dir; bundles record it)"
        )
        trunc = "  TRUNCATED" if m["run"]["truncated"] else ""
        print(
            f"{side}: {m['vm']}  {m['kernel']['release']}  "
            f"section={m['section']['name']}  corpus={corpus}{trunc}"
        )
    bg = base["section"]["geometry"] or {}
    cg = cand["section"]["geometry"] or {}
    drifted = sorted(k for k in set(bg) | set(cg) if bg.get(k) != cg.get(k))
    if drifted:
        print(f"note: section geometry differs in: {drifted}")
    print()


def _compare(base: dict, cand: dict) -> None:
    _banner(base, cand)

    universes = []
    for label, m in (("baseline", base), ("candidate", cand)):
        req = m["run"]["requested"]
        if req is None:
            why = "truncated" if m["run"]["truncated"] else "unknown"
            print(f"note: {label} requested set {why}; using recorded set")
            universes.append(set(m["tests"]))
        else:
            universes.append(set(req))
    b_universe, c_universe = universes

    scope = b_universe & c_universe
    buckets: dict[tuple[str, str], list[str]] = {}
    for t in sorted(scope):
        buckets.setdefault((_status(base, t), _status(cand, t)), []).append(t)

    n_common_fail = len(buckets.get(("failed", "failed"), []))

    def show(key: tuple[str, str], label: str, why: bool = False) -> None:
        tests = buckets.pop(key, [])
        if not tests:
            return
        print(f"{label} ({len(tests)}):")
        for t in tests:
            detail = _why(cand, t) or _why(base, t) if why else ""
            print(f"  {t}" + (f"  [{detail}]" if detail else ""))
        print()

    print(f"comparison universe: {len(scope)} tests (requested intersection)")
    print()
    show(("passed", "failed"), "REGRESSION CANDIDATES (pass -> fail)", True)
    show(("notrun", "failed"), "NEW FAILURES from previously-notrun", True)
    show(
        ("notrecorded", "failed"),
        "FAILURES among tests the baseline never recorded",
        True,
    )
    show(("failed", "passed"), "FIXED (fail -> pass)")
    show(("failed", "failed"), "COMMON FAILURES (fail in both)")
    show(("passed", "notrun"), "COVERAGE LOST (pass -> notrun)", True)
    show(("notrun", "passed"), "COVERAGE GAINED (notrun -> pass)")

    def norm(reason: str) -> str:
        return _DEV_RE.sub("<dev>", reason)

    both_notrun = buckets.pop(("notrun", "notrun"), [])
    drifted = [t for t in both_notrun if norm(_why(base, t)) != norm(_why(cand, t))]
    if drifted:
        print(f"NOTRUN REASON CHANGED ({len(drifted)}, device names ignored):")
        for t in drifted:
            print(f"  {t}: {_why(base, t)!r} -> {_why(cand, t)!r}")
        print()

    swings = []
    for t in buckets.get(("passed", "passed"), []):
        b_t = base["tests"][t].get("time_s")
        c_t = cand["tests"][t].get("time_s")
        if b_t is None or c_t is None or max(b_t, c_t) < 10:
            continue
        if min(b_t, c_t) * 2 <= max(b_t, c_t):
            swings.append((t, b_t, c_t))
    if swings:
        swings.sort(
            key=lambda s: max(s[1], s[2]) / max(min(s[1], s[2]), 1),
            reverse=True,
        )
        print(f"RUNTIME SWINGS among passing tests ({len(swings)}, advisory):")
        for t, b_t, c_t in swings[:20]:
            print(f"  {t}: {b_t}s -> {c_t}s")
        if len(swings) > 20:
            print(f"  ... and {len(swings) - 20} more")
        print()

    # A test both sides requested and NEITHER recorded is a record gap,
    # never "unchanged"; surface every notrecorded pairing that has no
    # named bucket above instead of absorbing it.
    gaps = {
        k: len(v)
        for k, v in buckets.items()
        if "notrecorded" in k and k != ("notrecorded", "notrecorded")
    }
    for k in gaps:
        buckets.pop(k)
    never = buckets.pop(("notrecorded", "notrecorded"), [])
    if gaps:
        print(f"RECORD GAPS (requested but not recorded on one side): {gaps}")
    if never:
        print(f"requested by both, recorded by neither: {len(never)}")
    if gaps or never:
        print()

    unchanged = sum(len(v) for k, v in buckets.items() if k[0] == k[1])
    unchanged += len(both_notrun) + n_common_fail
    leftover = {k: len(v) for k, v in buckets.items() if k[0] != k[1]}
    if leftover:
        print(f"other transitions: {leftover}")
    tail = (
        f" ({n_common_fail} of them the common failures above)" if n_common_fail else ""
    )
    print(f"unchanged: {unchanged} in-scope tests kept their status{tail}")

    for label, only, m, other in (
        ("baseline", b_universe - c_universe, base, cand),
        ("candidate", c_universe - b_universe, cand, base),
    ):
        if not only:
            continue
        by_status: dict[str, int] = {}
        for t in only:
            by_status[_status(m, t)] = by_status.get(_status(m, t), 0) + 1
        qualifier = (
            " (other side truncated; includes its lost tail)"
            if other["run"]["truncated"]
            else ""
        )
        print()
        print(f"requested only in {label} ({len(only)}){qualifier}, by status:")
        print(f"  {by_status}")
        for t in sorted(t for t in only if _status(m, t) == "failed"):
            print(f"  FAIL {t}  [{_why(m, t)}]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare two fstests runs from raw results directories."
    )
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--section", default="")
    parser.add_argument("--baseline-attempt", default="current")
    parser.add_argument("--candidate-attempt", default="current")
    parser.add_argument("--dump-manifests", type=Path)
    args = parser.parse_args()

    for side in (args.baseline, args.candidate):
        if not side.is_dir():
            sys.exit(f"error: {side} is not a directory")

    section = _detect_section(args.baseline, args.section)
    base = _build_manifest(args.baseline, section, args.baseline_attempt)
    cand = _build_manifest(args.candidate, section, args.candidate_attempt)

    if args.dump_manifests:
        args.dump_manifests.mkdir(parents=True, exist_ok=True)
        for name, m in (("baseline", base), ("candidate", cand)):
            out = args.dump_manifests / f"{name}.json"
            out.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
            print(f"wrote {out}")
        print()

    _compare(base, cand)


if __name__ == "__main__":
    main()
