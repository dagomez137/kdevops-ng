# SPDX-License-Identifier: copyleft-next-0.3.1
"""Aggregate the per-section xfstests results into one run verdict.

Folds the list of `f/fstests/collect` results (one per `[section]`, collected by the
flow's per-section forloop) into a single rollup: the per-section summaries, the
per-test rows, and a `status` of `failed` when any section reported a failure (or a
missing report), else `passed`. Pure aggregation, no guest contact. When a `vm_name`
is given it also writes the full rollup to `<share>/<kver>/report.json` (atomically),
so the run's verdict is recoverable from the share alone.

The returned value is a Windmill `render_all` display of three NATIVE tables (no
markdown: arrays of objects render as real sortable/searchable tables, while the
result-view markdown renderer has no GFM-table support):

  1. run info: testsuite, filesystem type, kernel, guest
  2. per section: the filesystem-under-test geometry + the section's pass/fail counts
  3. per test: one row per distinct test across all sections (failures first)

Equivalent command:

    cat "$WORKERS_DIR/shared/fstests/<vm>/<kver>/report.json"   # when vm_name is given
"""

from __future__ import annotations

import json

from f.fstests.common import _atomic_write, share_dir

# Cap the per-test table; the full per-test list is always in report.json.
_TEST_TABLE_CAP = 1000
_ICON = {"passed": "✅", "failed": "❌", "notrun": "⊘"}
# Realized xfs_info feature bits shown per section, the full set `xfs_report_geom()`
# (libfrog/fsgeom.c) emits as 0/1 flags, in its print order. Version fields (attr, naming/
# log version) and the raw geometry numerics ride along in report.json's detail.geometry.
_FEATURE_COLS = (
    "crc", "finobt", "sparse", "rmapbt", "reflink", "bigtime", "inobtcount", "nrext64",
    "exchange", "metadir", "projid32bit", "ascii-ci", "ftype", "parent", "lazy-count", "zoned",
)


def _per_test_rows(section: str, per_test: list[dict]) -> list[dict]:
    """One display row per distinct test (carrying its section): status icon, passes
    seen, fails (as a fraction of its passes on an `-i` run), summed wall-clock, and the
    failing message."""
    rows = []
    for t in per_test[:_TEST_TABLE_CAP]:
        runs = int(t.get("runs", 1) or 1)
        fails = int(t.get("fails", 0) or 0)
        rows.append({
            "section": section,
            "test": t.get("test"),
            "status": _ICON.get(t.get("status"), t.get("status", "")),
            "runs": runs,
            "fails": f"{fails}/{runs}" if runs > 1 else fails,
            "time(s)": t.get("time", 0),
            "message": t.get("message", ""),
        })
    return rows


def main(per_section: list[dict] | None = None, vm_name: str = "") -> dict:
    sections = list(per_section or [])
    # A section with no report (still running / crashed) is treated as failed so a
    # partial run never reports a false pass; `collect` already folds that into `status`.
    status = "failed" if any(s.get("status") == "failed" or not s.get("report_present", False)
                             for s in sections) else "passed"
    # Run-level facts (same across a run's sections): the kernel results are keyed by,
    # and the filesystem type under test.
    kernel_version = next((s.get("kernel_version") for s in sections if s.get("kernel_version")), "")
    fstype = next((s.get("fstype") for s in sections if s.get("fstype")), "")

    # Table 2: one row per section: the filesystem-under-test geometry (configured size +
    # the realized feature bits from xfs_info, when prepare captured them) then the counts.
    section_rows = []
    for s in sections:
        geo = (s.get("detail") or {}).get("geometry") or {}
        feat = geo.get("features") or {}
        section_rows.append({
            "section": s.get("section"),
            "bsize": geo.get("bsize"),
            "sectsize": geo.get("sectsize"),
            **{f: feat.get(f, "") for f in _FEATURE_COLS},
            "tests": int(s.get("tests", 0) or 0),
            "passed": int(s.get("passed", 0) or 0),
            "failed": int(s.get("failed", 0) or 0),
            "notrun": int(s.get("notrun", 0) or 0),
            "iterations": int(s.get("iterations", 1) or 1),
        })

    # Table 3: one row per distinct test across all sections, failures already first.
    test_rows = [r for s in sections
                 for r in _per_test_rows(s.get("section"), (s.get("detail") or {}).get("per_test") or [])]

    # report.json keeps the full structured rollup, including one flat row per failing test.
    failures = [{
        "section": s.get("section"), "test": f.get("name"), "fails": f.get("fails", 1),
        "iterations": int(s.get("iterations", 1) or 1), "type": f.get("type", ""),
        "message": f.get("message", ""),
    } for s in sections for f in ((s.get("detail") or {}).get("failures") or [])]
    rollup = {
        "status": status,
        "kernel_version": kernel_version,
        "fstype": fstype,
        "sections": sections,
        "failures": failures,
    }
    print(f"status={status} fstype={fstype or '?'} sections={len(sections)} "
          f"tests={sum(r['tests'] for r in section_rows)} failing={len(failures)}", flush=True)

    if vm_name:
        # Key the aggregate by kernel too (results are kver-keyed), so two kernels' runs
        # on one guest don't clobber each other's report.json. Fall back to the share root
        # when the kernel is unknown (degraded run).
        out_dir = share_dir(vm_name) / kernel_version if kernel_version else share_dir(vm_name)
        path = out_dir / "report.json"
        _atomic_write(path, json.dumps(rollup, indent=2) + "\n")
        print(f"+ wrote {path}", flush=True)
        rollup["report_json"] = str(path)

    # render_all (must be the sole key): three native tables, no markdown: run info,
    # the per-section filesystem geometry + counts, then one row per test.
    run_info = [{"testsuite": "fstests", "fstype": fstype or "?",
                 "kernel": kernel_version or "?", "guest": vm_name or "?"}]
    return {"render_all": [run_info, section_rows, test_rows]}
