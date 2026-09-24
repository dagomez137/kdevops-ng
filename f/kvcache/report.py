# SPDX-License-Identifier: copyleft-next-0.3.1
"""Summarise a KV cache run for the job result table.

Takes the collect step's output and flattens the headline numbers (TTFT
percentiles first: TTFT is the number prefix-cache hits move) plus the cache
counter deltas into one flat dict Windmill renders as the flow's result.
Pure function of its inputs; touches neither host nor guest.
"""

from __future__ import annotations


def main(collected: dict, wait: dict | None = None) -> dict:
    if not collected.get("collected"):
        return {
            "status": "incomplete",
            "vm": collected.get("vm"),
            "preset": collected.get("preset"),
            "results_dir": collected.get("results_dir"),
        }
    s = collected["summary"]
    cache = s.get("cache_metrics_delta", {}) or {}
    row = {
        "status": "ok" if (wait or {}).get("success", True) else "failed",
        "vm": collected.get("vm"),
        "preset": s.get("preset"),
        "model": s.get("model"),
        "sessions": s.get("sessions"),
        "sessions_failed": s.get("sessions_failed"),
        "turns": s.get("turns_completed"),
        "wall_seconds": s.get("wall_seconds"),
        "ttft_p50_s": s.get("ttft_p50"),
        "ttft_p95_s": s.get("ttft_p95"),
        "ttft_p99_s": s.get("ttft_p99"),
        "latency_p50_s": s.get("latency_p50"),
        "latency_p95_s": s.get("latency_p95"),
        "prompt_tokens": s.get("prompt_tokens"),
        "completion_tokens": s.get("completion_tokens"),
        "results_dir": collected.get("results_dir"),
    }
    # Cache counters, prefixed so they group in the rendered table.
    for k in sorted(cache):
        row[f"cache.{k}"] = cache[k]
    print("\n".join(f"{k}: {v}" for k, v in row.items()), flush=True)
    return row
