#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Replay agentic multi-turn traces against an OpenAI-compatible endpoint.

Port of kdevops' vllm-benchmark-v3.py.j2 off Jinja (config comes from CLI
flags and the environment -- the kvcache-bench@ unit's EnvironmentFile --
instead of template substitution), with the review fixes applied:

  * TTFT is real time-to-first-token: requests stream, and the first SSE
    chunk's arrival is timed. The old stream=False number (full response
    latency) is kept separately as `latency`.
  * Unknown presets fail with the valid list instead of a KeyError.
  * Failed sessions are counted and reported; past --max-failure-rate the
    process exits non-zero, so a run where most sessions died can never
    land as a plausible-looking summary (and the systemd unit's
    Result/ExecMainStatus reflect it).
  * The engine's /metrics is scraped before and after the replay; the
    lmcache_* and vllm prefix-cache counter deltas land in the summary, so
    "fast because cached" is distinguishable from "fast because short".

Environment (all optional; the unit's EnvironmentFile sets them):
  KVBENCH_ENDPOINT   default http://127.0.0.1:8000
  KVBENCH_MODEL      default: first model the endpoint lists
  KVBENCH_DATASET    default sammshen/lmcache-agentic-traces
  KVBENCH_SOURCE     dataset source filter column value (optional)
  KVBENCH_SESSIONS   session cap, 0 = all (default 0)
  KVBENCH_WORKERS    concurrent sessions (default 4)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import aiohttp

# preset -> (concurrency multiplier, gap scale, mode). Gap scale compresses
# the recorded inter-turn think time; max-pressure ignores gaps entirely.
PRESETS: dict[str, tuple[int, float, str]] = {
    "swebench-realistic": (1, 1.0, "paced"),
    "swebench-burst": (2, 0.25, "paced"),
    "gaia-realistic": (1, 1.0, "paced"),
    "gaia-burst": (2, 0.25, "paced"),
    "wildclaw-heavy": (4, 0.1, "paced"),
    "max-pressure": (8, 0.0, "flood"),
}

_METRIC_RE = re.compile(
    r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+([0-9.eE+-]+)\s*$"
)
_METRIC_PREFIXES = ("lmcache_", "vllm:prefix_cache", "vllm:gpu_prefix_cache")


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, round(p / 100 * (len(sorted_vals) - 1))))
    return sorted_vals[k]


async def _scrape_metrics(
    session: aiohttp.ClientSession, endpoint: str
) -> dict[str, float]:
    """Sum the cache-relevant counters from the engine's /metrics, by name.

    Labels are folded (summed) per metric name: the summary needs deltas, not
    per-label series. A missing /metrics is tolerated (empty dict) so the
    replay itself never fails on telemetry."""
    out: dict[str, float] = {}
    try:
        async with session.get(f"{endpoint}/metrics") as resp:
            text = await resp.text()
    except Exception as exc:  # noqa: BLE001 - telemetry is best-effort
        print(f"metrics scrape failed ({exc}); continuing", flush=True)
        return out
    for line in text.splitlines():
        m = _METRIC_RE.match(line)
        if not m or not m.group(1).startswith(_METRIC_PREFIXES):
            continue
        try:
            out[m.group(1)] = out.get(m.group(1), 0.0) + float(m.group(2))
        except ValueError:
            continue
    return out


async def _first_model(session: aiohttp.ClientSession, endpoint: str) -> str:
    async with session.get(f"{endpoint}/v1/models") as resp:
        data = await resp.json()
    models = [m["id"] for m in data.get("data", [])]
    if not models:
        raise RuntimeError(f"{endpoint}/v1/models lists no models")
    return models[0]


def _load_sessions(dataset: str, source: str, cap: int) -> list[list[dict]]:
    """Group the trace dataset into ordered per-session turn lists.

    Filters on the dataset's own source column when a filter is given (never
    by pattern-matching session ids). Each turn needs `session_id`,
    `turn_index`, `prompt`, `gap_seconds` (missing gaps read as 0)."""
    from datasets import load_dataset  # deferred: heavy import

    ds = load_dataset(dataset, split="train")
    rows = (r for r in ds if not source or str(r.get("source", "")) == source)
    sessions: dict[str, list[dict]] = {}
    for r in rows:
        sessions.setdefault(str(r["session_id"]), []).append(r)
    ordered = [
        sorted(turns, key=lambda t: int(t.get("turn_index", 0)))
        for _, turns in sorted(sessions.items())
    ]
    return ordered[:cap] if cap else ordered


async def _replay_turn(
    session: aiohttp.ClientSession, endpoint: str, model: str, prompt: str
) -> dict:
    """One streamed chat completion; returns ttft/latency/token counts."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(os.environ.get("KVBENCH_MAX_TOKENS", "256")),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    t0 = time.monotonic()
    ttft = None
    usage: dict = {}
    async with session.post(f"{endpoint}/v1/chat/completions", json=body) as resp:
        resp.raise_for_status()
        async for raw in resp.content:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            if ttft is None:
                ttft = time.monotonic() - t0
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
    latency = time.monotonic() - t0
    return {
        "ttft": ttft if ttft is not None else latency,
        "latency": latency,
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
    }


async def _replay_session(
    sem: asyncio.Semaphore,
    session: aiohttp.ClientSession,
    endpoint: str,
    model: str,
    turns: list[dict],
    gap_scale: float,
    mode: str,
    results: list[dict],
    failures: list[str],
) -> None:
    sid = str(turns[0]["session_id"]) if turns else "?"
    async with sem:
        for turn in turns:
            if mode == "paced" and gap_scale > 0:
                await asyncio.sleep(float(turn.get("gap_seconds", 0) or 0) * gap_scale)
            try:
                r = await _replay_turn(session, endpoint, model, str(turn["prompt"]))
            except Exception as exc:  # noqa: BLE001 - accounted, never silent
                failures.append(f"{sid}#{turn.get('turn_index')}: {exc}")
                print(
                    f"session {sid} aborted at turn {turn.get('turn_index')}: {exc}",
                    flush=True,
                )
                return
            r["session_id"] = sid
            r["turn_index"] = int(turn.get("turn_index", 0))
            results.append(r)


async def _run(args: argparse.Namespace) -> int:
    mult, gap_scale, mode = PRESETS[args.preset]
    endpoint = os.environ.get("KVBENCH_ENDPOINT", "http://127.0.0.1:8000").rstrip("/")
    cap = int(os.environ.get("KVBENCH_SESSIONS", "0"))
    workers = int(os.environ.get("KVBENCH_WORKERS", "4")) * mult
    dataset = os.environ.get("KVBENCH_DATASET", "sammshen/lmcache-agentic-traces")
    source = os.environ.get("KVBENCH_SOURCE", "")

    sessions = _load_sessions(dataset, source, cap)
    if not sessions:
        print(
            f"dataset {dataset!r} (source={source!r}) yields no sessions",
            file=sys.stderr,
        )
        return 2
    print(
        f"preset={args.preset} mode={mode} gap_scale={gap_scale} workers={workers} "
        f"sessions={len(sessions)} endpoint={endpoint}",
        flush=True,
    )

    timeout = aiohttp.ClientTimeout(total=None, sock_read=600)
    results: list[dict] = []
    failures: list[str] = []
    async with aiohttp.ClientSession(timeout=timeout) as http:
        model = os.environ.get("KVBENCH_MODEL") or await _first_model(http, endpoint)
        metrics_before = await _scrape_metrics(http, endpoint)
        t0 = time.monotonic()
        sem = asyncio.Semaphore(workers)
        await asyncio.gather(
            *(
                _replay_session(
                    sem,
                    http,
                    endpoint,
                    model,
                    turns,
                    gap_scale,
                    mode,
                    results,
                    failures,
                )
                for turns in sessions
            )
        )
        wall = time.monotonic() - t0
        metrics_after = await _scrape_metrics(http, endpoint)

    ttfts = sorted(r["ttft"] for r in results)
    lats = sorted(r["latency"] for r in results)
    failure_rate = len(failures) / len(sessions)
    summary = {
        "preset": args.preset,
        "model": model,
        "dataset": dataset,
        "source": source or None,
        "sessions": len(sessions),
        "sessions_failed": len(failures),
        "failure_rate": round(failure_rate, 4),
        "turns_completed": len(results),
        "wall_seconds": round(wall, 3),
        "ttft_p50": round(_percentile(ttfts, 50), 4),
        "ttft_p95": round(_percentile(ttfts, 95), 4),
        "ttft_p99": round(_percentile(ttfts, 99), 4),
        "latency_p50": round(_percentile(lats, 50), 4),
        "latency_p95": round(_percentile(lats, 95), 4),
        "completion_tokens": sum(r["completion_tokens"] for r in results),
        "prompt_tokens": sum(r["prompt_tokens"] for r in results),
        "cache_metrics_delta": {
            k: round(metrics_after.get(k, 0.0) - metrics_before.get(k, 0.0), 3)
            for k in sorted(set(metrics_before) | set(metrics_after))
        },
    }

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "turns.json").write_text(json.dumps(results) + "\n")
    (out / "failures.json").write_text(json.dumps(failures, indent=1) + "\n")
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1), flush=True)

    if failure_rate > args.max_failure_rate:
        print(
            f"FAIL: {len(failures)}/{len(sessions)} sessions failed "
            f"(rate {failure_rate:.2%} > --max-failure-rate {args.max_failure_rate:.2%})",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preset", required=True)
    ap.add_argument("--output", required=True, help="results directory (created)")
    ap.add_argument("--max-failure-rate", type=float, default=0.05)
    args = ap.parse_args()
    if args.preset not in PRESETS:
        ap.error(f"unknown preset {args.preset!r}; valid: {', '.join(sorted(PRESETS))}")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
