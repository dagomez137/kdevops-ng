# SPDX-License-Identifier: copyleft-next-0.3.1
"""Collect one blktests group's results from the `blktests` share (read-only).

The guest's `./check` writes one TSV file per test at
`results/<devdir>/<group>/<nnn>` under the share's `<kver>/results` tree.
`<devdir>` is `nodev` or a device basename, optionally suffixed by a
set_conditions variant (`nodev_tr_tcp_bd_file`), so ONE test number can yield
SEVERAL result rows; every row is reported, keyed (`devdir`, `group/nnn`).
`f/blktests/start` removed the group's previous result subtrees before
starting, so anything present is this run's. The verdict is gated by the run's
outcome from `f/blktests/wait`: a crashed guest, a timed-out group, or a unit
that did not finish cleanly is `failed` even when plausible rows exist, and
zero rows is `notrun`, never a pass (a failed `group_requires` prints one
stdout-only line, writes no files, and exits 0; its skip reason is in the
streamed journal, not in any file). Read-only; the host never contacts the
guest.

Equivalent command:

    cat "$WORKERS_DIR/shared/blktests/<vm>/<kver>/results/"*/<group>/[0-9][0-9][0-9]
"""

from __future__ import annotations

from f.blktests.common import collect_group_rows, group_status, results_dir
from f.blktests.common import list_vms as _list_vms

# Failures first (a `missing` row is a truncated result, failure-adjacent),
# then the notruns, then the passes; within a rank by (devdir, test).
_RANK = {"fail": 0, "missing": 1, "not run": 2, "pass": 3}


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(
    vm_name: str,
    group: str,
    kernel_version: str,
    unit_ok: bool = False,
    crashed: bool = False,
    timed_out: bool = False,
    started_realtime_ms: int | None = None,
    ended_realtime_ms: int | None = None,
) -> dict:
    results_root = results_dir(vm_name, kernel_version)
    print(f"+ reading {results_root}", flush=True)
    rows = collect_group_rows(results_root, group)
    rows.sort(
        key=lambda r: (
            _RANK.get(r.get("status"), 0),
            r.get("devdir") or "",
            r.get("test") or "",
        )
    )
    stats = {
        "pass": sum(r["status"] == "pass" for r in rows),
        "fail": sum(r["status"] not in ("pass", "not run") for r in rows),
        "notrun": sum(r["status"] == "not run" for r in rows),
    }
    status = group_status(rows, unit_ok, crashed, timed_out)
    print(
        f"group {group}: status={status} rows={len(rows)} pass={stats['pass']} "
        f"fail={stats['fail']} notrun={stats['notrun']} "
        f"(unit_ok={unit_ok} crashed={crashed} timed_out={timed_out})",
        flush=True,
    )
    return {
        "group": group,
        "vm_name": vm_name,
        "kernel_version": kernel_version,
        "status": status,
        "crashed": crashed,
        "timed_out": timed_out,
        "started_realtime_ms": started_realtime_ms,
        "ended_realtime_ms": ended_realtime_ms,
        "rows": rows,
        "stats": stats,
    }
