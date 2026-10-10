# SPDX-License-Identifier: copyleft-next-0.3.1
"""Collect one replay's numbers from the `kvcache` share (read-only).

Reads `<share>/<kver>/results/<preset>/summary.json`, which kvcache-bench
writes when the replay ends: time to first token and latency percentiles,
token counts, session failures by reason, and the server's KV cache counter
deltas. f/kvcache/start removed the preset's previous results, so a summary
present is this run's. The verdict is gated by the run's outcome from
f/kvcache/wait: a crashed guest, a timed-out replay, or a unit that did not
finish cleanly is `failed` even with a summary, no summary or no completed turn
is `notrun`, and a session failure rate above `max_failure_rate` is `failed`,
where a session that outgrew the model's context window does not count as
failed (`f.kvcache.common.item_status`). A `reason` from wait (a replay
that never ran because the server did not come up) is carried into the
result. Read-only; the host never contacts the guest.

Equivalent command:

    cat "$WORKERS_DIR/shared/kvcache/<vm>/<kver>/results/<preset>/summary.json"
"""

from __future__ import annotations

import json

from f.kvcache.common import (
    check_failure_rate,
    counted_failure_rate,
    item_status,
    results_dir,
)
from f.kvcache.common import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(
    vm_name: str,
    preset: str,
    kernel_version: str,
    unit_ok: bool = False,
    crashed: bool = False,
    timed_out: bool = False,
    max_failure_rate: float = 0.05,
    started_realtime_ms: int | None = None,
    ended_realtime_ms: int | None = None,
    reason: str = "",
) -> dict:
    max_failure_rate = check_failure_rate(max_failure_rate)
    path = results_dir(vm_name, kernel_version, preset) / "summary.json"
    print(f"reading {path}", flush=True)
    try:
        summary = json.loads(path.read_text())
    except (OSError, ValueError):
        summary = None
    status = item_status(summary, unit_ok, crashed, timed_out, max_failure_rate)
    rate = round(counted_failure_rate(summary), 4) if summary else None
    print(
        f"preset {preset}: status={status} "
        f"turns={(summary or {}).get('turns_completed')} "
        f"failure_rate={rate} "
        f"(unit_ok={unit_ok} crashed={crashed} timed_out={timed_out})"
        + (f": {reason}" if reason else ""),
        flush=True,
    )
    return {
        "preset": preset,
        "vm_name": vm_name,
        "kernel_version": kernel_version,
        "status": status,
        "crashed": crashed,
        "timed_out": timed_out,
        "reason": reason,
        "started_realtime_ms": started_realtime_ms,
        "ended_realtime_ms": ended_realtime_ms,
        "summary": summary or {},
    }
