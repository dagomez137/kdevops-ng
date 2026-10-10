#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
"""Replay recorded multi-turn agent sessions against an OpenAI-compatible server.

Each row of the trace dataset is one turn: `session_id`, `input` (the whole
message list the agent sent, so a session's prompts grow by prefix),
`output_length` (the tokens the recorded model produced) and `pre_gap` (the
seconds the agent waited before sending it). A session's turns are replayed in
recorded order, so the prefix each turn shares with the last stays reusable by
the server's KV cache. A fixed pool of workers each replays one session at a
time, recorded gaps included, so `workers` is the number of sessions in
flight, not of requests.

A preset picks the sessions, by the source a session id starts with, and the
timing: recorded gaps, or none for maximum pressure. Each turn is a streamed
chat completion asking for the recorded output length with end-of-sequence
ignored, so every run generates the same number of tokens; the arrival of the
first streamed chunk is the time to first token.

The server's /metrics counters are read before and after the replay and their
deltas land in the summary, which tells a fast run that hit the cache from one
that sent short prompts; when either read fails the deltas are null and the
summary names the failed read. Writes summary.json, turns.json and
failures.json to the output directory. The exit status says whether a summary
was written, not whether the numbers are good: 0 when it was, 2 when there was
nothing to replay, 1 when the server named no model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import aiohttp

# name -> (session source, honour recorded gaps, description)
PRESETS: dict[str, tuple[str | None, bool, str]] = {
    "mixed-realistic": (None, True, "every source, recorded gaps"),
    "swebench-realistic": ("swebench", True, "code-repair sessions, recorded gaps"),
    "swebench-burst": ("swebench", False, "code-repair sessions, no gaps"),
    "gaia-realistic": ("gaia", True, "research sessions, recorded gaps"),
    "gaia-burst": ("gaia", False, "research sessions, no gaps"),
    "wildclaw-heavy": ("wildclaw", True, "long-context tool sessions, recorded gaps"),
    "max-pressure": (None, False, "every source, no gaps"),
}

_METRIC_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+(\S+)\s*$")
_METRIC_PREFIXES = ("lmcache:", "lmcache_", "vllm:prefix_cache", "vllm:gpu_prefix")
_CONTEXT_ERRORS = ("maximum context length", "is longer than the maximum model length")


class ProtocolError(Exception):
    """The server streamed a chunk that is not the chat completion shape."""


def percentile(sorted_vals: list[float], p: float) -> float:
    """Nearest-rank percentile of an ascending list; 0.0 for none."""
    if not sorted_vals:
        return 0.0
    rank = math.ceil(p / 100 * len(sorted_vals))
    return sorted_vals[max(0, min(len(sorted_vals), rank) - 1)]


def session_source(session_id: str) -> str:
    """The source a session came from: its id up to the first `__`."""
    return session_id.split("__", 1)[0]


def group_sessions(rows, source: str | None, cap: int) -> list[list[dict]]:
    """Rows grouped into sessions in dataset order, filtered by source.

    A session's rows are stored together, so with a cap the rows stop being
    read at the first row of the session after the last one kept.
    """
    sessions: dict[str, list[dict]] = {}
    for row in rows:
        sid = str(row["session_id"])
        if source and session_source(sid) != source:
            continue
        if cap and sid not in sessions and len(sessions) == cap:
            break
        sessions.setdefault(sid, []).append(row)
    return list(sessions.values())


def messages(row: dict) -> list[dict]:
    """The turn's messages without the fields the dataset stores as null.

    Every message carries `tool_calls`, `tool_call_id` and `name`, null where
    unused, and the server's request validation refuses a null there.
    """
    return [{k: v for k, v in m.items() if v is not None} for m in row["input"]]


def failure_reason(error: str) -> str:
    """`context` when the turn outgrew the served context window, else `error`."""
    return "context" if any(e in error for e in _CONTEXT_ERRORS) else "error"


def parse_metrics(text: str) -> dict[str, float]:
    """The cache counters in a Prometheus text page, summed over labels.

    Only `_total` samples are counters; gauges, histogram buckets and the
    `_created` timestamps would not difference into a count.
    """
    out: dict[str, float] = {}
    for line in text.splitlines():
        m = _METRIC_RE.match(line)
        if not m or not m.group(1).startswith(_METRIC_PREFIXES):
            continue
        if not m.group(1).endswith("_total"):
            continue
        try:
            out[m.group(1)] = out.get(m.group(1), 0.0) + float(m.group(2))
        except ValueError:
            continue
    return out


async def scrape_metrics(
    http: aiohttp.ClientSession, endpoint: str
) -> tuple[dict[str, float] | None, str | None]:
    """The cache counters, or None and the reason the read failed."""
    try:
        async with http.get(f"{endpoint}/metrics") as resp:
            if resp.status != 200:
                error = f"HTTP {resp.status}"
            else:
                return parse_metrics(await resp.text()), None
    except aiohttp.ClientError as exc:
        error = str(exc) or type(exc).__name__
    print(f"no metrics from {endpoint}/metrics: {error}", flush=True)
    return None, error


def metrics_delta(
    before: dict[str, float] | None, after: dict[str, float] | None
) -> dict[str, float] | None:
    """Per-counter change over the replay; None unless both reads succeeded."""
    if before is None or after is None:
        return None
    return {
        k: round(after.get(k, 0.0) - before.get(k, 0.0), 3)
        for k in sorted(set(before) | set(after))
    }


def _token_count(usage: dict, key: str) -> int:
    value = usage.get(key)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"usage {key} is {value!r}") from exc


async def first_model(http: aiohttp.ClientSession, endpoint: str) -> str:
    async with http.get(f"{endpoint}/v1/models") as resp:
        resp.raise_for_status()
        models = [m["id"] for m in (await resp.json()).get("data", [])]
    if not models:
        raise RuntimeError(f"{endpoint}/v1/models lists no model")
    return models[0]


async def replay_turn(
    http: aiohttp.ClientSession, endpoint: str, model: str, row: dict, cap: int
) -> dict:
    """One streamed chat completion: time to first token, latency, tokens."""
    max_tokens = int(row.get("output_length") or 1)
    if cap:
        max_tokens = min(max_tokens, cap)
    body = {
        "model": model,
        "messages": messages(row),
        "max_tokens": max_tokens,
        "ignore_eos": True,
        "temperature": 0.0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    t0 = time.monotonic()
    ttft = None
    usage: dict = {}
    async with http.post(f"{endpoint}/v1/chat/completions", json=body) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
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
            except ValueError as exc:
                raise ProtocolError(f"chunk is not JSON: {payload[:200]}") from exc
            if not isinstance(chunk, dict):
                raise ProtocolError(f"chunk is not an object: {payload[:200]}")
            if err := chunk.get("error"):
                raise RuntimeError(
                    str(err.get("message", err) if isinstance(err, dict) else err)
                )
            if chunk.get("usage"):
                usage = chunk["usage"]
                if not isinstance(usage, dict):
                    raise ProtocolError(f"usage is not an object: {usage!r}")
    if ttft is None:
        raise RuntimeError("the stream ended before its first chunk")
    latency = time.monotonic() - t0
    return {
        "ttft": ttft,
        "latency": latency,
        "prompt_tokens": _token_count(usage, "prompt_tokens"),
        "completion_tokens": _token_count(usage, "completion_tokens"),
    }


async def replay_session(
    sem, http, endpoint, model, turns, gaps, cap, results, failures
) -> None:
    """Replay one session's turns in order; the first failure ends it."""
    sid = str(turns[0]["session_id"])
    async with sem:
        for index, row in enumerate(turns):
            if gaps and index:
                await asyncio.sleep(float(row.get("pre_gap") or 0.0))
            try:
                r = await replay_turn(http, endpoint, model, row, cap)
            except (
                aiohttp.ClientError,
                RuntimeError,
                ValueError,
                ProtocolError,
            ) as exc:
                reason = (
                    "protocol"
                    if isinstance(exc, ProtocolError)
                    else failure_reason(str(exc))
                )
                failures.append(
                    {
                        "session_id": sid,
                        "turn": index,
                        "reason": reason,
                        "error": str(exc),
                    }
                )
                print(f"{sid} turn {index}: {exc}", flush=True)
                return
            results.append({"session_id": sid, "turn": index, **r})


def load_rows(dataset: str, revision: str | None, streaming: bool):
    from datasets import load_dataset

    return load_dataset(dataset, revision=revision, split="train", streaming=streaming)


async def run(args: argparse.Namespace) -> int:
    source, gaps, _ = PRESETS[args.preset]
    revision = args.dataset_revision or None
    rows = load_rows(args.dataset, revision, streaming=bool(args.sessions))
    sessions = group_sessions(rows, source, args.sessions)
    if not sessions:
        print(f"{args.dataset}: no session for preset {args.preset}", file=sys.stderr)
        return 2
    endpoint = args.endpoint.rstrip("/")
    print(
        f"preset={args.preset} source={source or 'all'} gaps={gaps} "
        f"sessions={len(sessions)} workers={args.workers} endpoint={endpoint}",
        flush=True,
    )
    timeout = aiohttp.ClientTimeout(total=None, sock_read=600)
    results: list[dict] = []
    failures: list[dict] = []
    async with aiohttp.ClientSession(timeout=timeout) as http:
        try:
            model = args.model or await first_model(http, endpoint)
        except (aiohttp.ClientError, RuntimeError) as exc:
            print(f"cannot pick a model from {endpoint}: {exc}", file=sys.stderr)
            return 1
        before, before_error = await scrape_metrics(http, endpoint)
        t0 = time.monotonic()
        sem = asyncio.Semaphore(args.workers)
        await asyncio.gather(
            *(
                replay_session(
                    sem,
                    http,
                    endpoint,
                    model,
                    s,
                    gaps,
                    args.max_tokens,
                    results,
                    failures,
                )
                for s in sessions
            )
        )
        wall = time.monotonic() - t0
        after, after_error = await scrape_metrics(http, endpoint)

    order = {str(s[0]["session_id"]): i for i, s in enumerate(sessions)}
    results.sort(key=lambda r: (order[r["session_id"]], r["turn"]))

    ttfts = sorted(r["ttft"] for r in results)
    latencies = sorted(r["latency"] for r in results)
    summary = {
        "preset": args.preset,
        "source": source,
        "recorded_gaps": gaps,
        "model": model,
        "dataset": args.dataset,
        "dataset_revision": revision,
        "workers": args.workers,
        "sessions": len(sessions),
        "sessions_failed": len(failures),
        "failure_rate": round(len(failures) / len(sessions), 4),
        "failures_by_reason": {
            reason: sum(1 for f in failures if f["reason"] == reason)
            for reason in sorted({f["reason"] for f in failures})
        },
        "turns": sum(len(s) for s in sessions),
        "turns_completed": len(results),
        "wall_seconds": round(wall, 3),
        "ttft_p50": round(percentile(ttfts, 50), 4),
        "ttft_p95": round(percentile(ttfts, 95), 4),
        "ttft_p99": round(percentile(ttfts, 99), 4),
        "latency_p50": round(percentile(latencies, 50), 4),
        "latency_p95": round(percentile(latencies, 95), 4),
        "prompt_tokens": sum(r["prompt_tokens"] for r in results),
        "completion_tokens": sum(r["completion_tokens"] for r in results),
        "cache_metrics_delta": metrics_delta(before, after),
        "metrics_errors": {
            k: v for k, v in (("before", before_error), ("after", after_error)) if v
        },
    }
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "turns.json").write_text(json.dumps(results) + "\n")
    (out / "failures.json").write_text(json.dumps(failures, indent=1) + "\n")
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1), flush=True)
    return 0


def main() -> int:
    env = os.environ.get
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list-presets", action="store_true", help="print presets as JSON")
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--output", help="results directory, created")
    ap.add_argument(
        "--endpoint", default=env("KVBENCH_ENDPOINT", "http://127.0.0.1:8000")
    )
    ap.add_argument("--model", default=env("KVBENCH_MODEL", ""))
    ap.add_argument(
        "--dataset", default=env("KVBENCH_DATASET", "sammshen/lmcache-agentic-traces")
    )
    ap.add_argument(
        "--dataset-revision",
        default=env("KVBENCH_DATASET_REVISION", ""),
        help="dataset commit, branch or tag; empty reads the default branch",
    )
    # argparse applies type= to a string default, so a bad environment value
    # is reported like a bad option.
    ap.add_argument("--sessions", type=int, default=env("KVBENCH_SESSIONS", "0"))
    ap.add_argument("--workers", type=int, default=env("KVBENCH_WORKERS", "4"))
    ap.add_argument(
        "--max-tokens",
        type=int,
        default=env("KVBENCH_MAX_TOKENS", "0"),
        help="cap on each turn's recorded output length; 0 keeps it",
    )
    args = ap.parse_args()
    if args.workers < 1:
        ap.error("--workers must be at least 1")
    if args.sessions < 0 or args.max_tokens < 0:
        ap.error("--sessions and --max-tokens take 0 or more")
    if args.list_presets:
        print(json.dumps({k: v[2] for k, v in PRESETS.items()}))
        return 0
    if not args.preset or not args.output:
        ap.error("--preset and --output are required")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
