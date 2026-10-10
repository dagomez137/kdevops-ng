# SPDX-License-Identifier: copyleft-next-0.3.1
"""Aggregate the per-preset replays into one run report.

Folds the f/kvcache/collect results (one per preset, from the flow's loop) into
a rollup with a `status` of `passed` only when every preset passed; an empty
list never reads as a pass. `run_status` in f/kvcache/common is the one rule
f/kvcache/judge shares. When a `vm_name` is given the rollup is also written to
`<share>/<kver>/report.json` (atomically), so the numbers outlive the job.

The returned value is a Windmill `render_all` display of three native tables
(`render_all` must stay the sole key or the display falls back to raw JSON):

  1. run info: testsuite, kernel, guest, model
  2. per preset: verdict, sessions, time to first token and latency
     percentiles, the prefix cache hit rate, wall time
  3. per preset: every KV cache counter delta the server reported

Equivalent command:

    cat "$WORKERS_DIR/shared/kvcache/<vm>/<kver>/report.json"   # when vm_name is given
"""

from __future__ import annotations

import json

from f.kvcache.common import _atomic_write, report_path, run_status

_ICON = {"passed": "✅", "failed": "❌", "notrun": "⊘"}


def _hit_rate(cache: dict) -> float | None:
    queries = cache.get("vllm:prefix_cache_queries_total") or 0
    hits = cache.get("vllm:prefix_cache_hits_total") or 0
    return round(100 * hits / queries, 1) if queries else None


def main(per_preset: list[dict] | None = None, vm_name: str = "") -> dict:
    presets = list(per_preset or [])
    status = run_status(presets)
    entries = [p if isinstance(p, dict) else {} for p in presets]
    kernel_version = next(
        (e.get("kernel_version") for e in entries if e.get("kernel_version")), ""
    )
    model = next(
        ((e.get("summary") or {}).get("model") for e in entries if e.get("summary")),
        "",
    )

    preset_rows = []
    counter_rows = []
    for e in entries:
        s = e.get("summary") or {}
        cache = s.get("cache_metrics_delta") or {}
        name = e.get("preset") or "(step failed)"
        preset_rows.append(
            {
                "preset": name,
                "status": _ICON.get(e.get("status", ""), "❌"),
                "sessions": s.get("sessions", 0),
                "failed": s.get("sessions_failed", 0),
                "failed by reason": ", ".join(
                    f"{k} {v}" for k, v in (s.get("failures_by_reason") or {}).items()
                ),
                "turns": s.get("turns_completed", 0),
                "TTFT p50 (s)": s.get("ttft_p50"),
                "TTFT p95 (s)": s.get("ttft_p95"),
                "TTFT p99 (s)": s.get("ttft_p99"),
                "latency p50 (s)": s.get("latency_p50"),
                "latency p95 (s)": s.get("latency_p95"),
                "prefix hit %": _hit_rate(cache),
                "wall (s)": s.get("wall_seconds"),
                "crashed": bool(e.get("crashed", False)),
                "timed_out": bool(e.get("timed_out", False)),
            }
        )
        counter_rows += [
            {"preset": name, "counter": k, "delta": v} for k, v in sorted(cache.items())
        ]

    rollup = {
        "status": status,
        "kernel_version": kernel_version,
        "model": model,
        "presets": presets,
    }
    print(f"status={status} presets={len(presets)} model={model or '?'}", flush=True)
    if vm_name:
        path = report_path(vm_name, kernel_version)
        _atomic_write(path, json.dumps(rollup, indent=2) + "\n")
        print(f"wrote {path}", flush=True)

    run_info = [
        {
            "testsuite": "kvcache",
            "kernel": kernel_version or "?",
            "guest": vm_name or "?",
            "model": model or "?",
        }
    ]
    return {"render_all": [run_info, preset_rows, counter_rows]}
