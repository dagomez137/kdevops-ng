# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for kvcache-bench's pure logic (the vendored replayer)."""

import argparse
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

BENCH = (
    Path(__file__).resolve().parent.parent
    / "vendor/nixos-flake/pkgs/kvcache-bench/kvcache-bench.py"
)


def _aiohttp_stub():
    stub = types.ModuleType("aiohttp")
    vars(stub).update(
        ClientError=type("ClientError", (Exception,), {}),
        ClientTimeout=lambda **kwargs: None,
    )
    return stub


@pytest.fixture(scope="module")
def bench():
    # A stand-in HTTP client keeps the tests free of the dependency; it is
    # visible only while the module loads, so other test modules see the real
    # sys.modules.
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "aiohttp", _aiohttp_stub())
        spec = importlib.util.spec_from_file_location("kvcache_bench", BENCH)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class _Response:
    def __init__(self, status=200, body="", lines=()):
        self.status = status
        self._body = body
        self.content = self._stream(lines)

    async def _stream(self, lines):
        for line in lines:
            yield line.encode() + b"\n"

    async def text(self):
        return self._body

    async def json(self):
        return json.loads(self._body)

    def raise_for_status(self):
        if self.status != 200:
            raise RuntimeError(f"HTTP {self.status}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _HTTP:
    """Answers each path from a queue of responses, in request order."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}

    def _next(self, url):
        path = url.split("127.0.0.1", 1)[1]
        return self.routes[path].pop(0)

    def get(self, url):
        return self._next(url)

    def post(self, url, json=None):
        return self._next(url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _sse(*chunks):
    return [f"data: {c if isinstance(c, str) else json.dumps(c)}" for c in chunks]


def _turn(bench, *lines, status=200, body=""):
    http = _HTTP({"/v1/chat/completions": [_Response(status, body=body, lines=lines)]})
    return asyncio.run(
        bench.replay_turn(http, "http://127.0.0.1", "m", _row("gaia__a"), 0)
    )


def _row(sid, turn=0):
    return {
        "session_id": sid,
        "input": [],
        "output_length": 8,
        "pre_gap": 0.0,
        "t": turn,
    }


def test_a_sessions_source_is_its_id_prefix(bench):
    assert bench.session_source("gaia__L2_b2c257e0__claude") == "gaia"
    assert bench.session_source("plain") == "plain"


def test_sessions_group_in_dataset_order_filtered_by_source(bench):
    rows = [
        _row("swebench__a", 0),
        _row("swebench__a", 1),
        _row("gaia__b", 0),
        _row("swebench__c", 0),
    ]
    got = bench.group_sessions(rows, "swebench", 0)
    assert [[r["t"] for r in s] for s in got] == [[0, 1], [0]]
    assert [s[0]["session_id"] for s in got] == ["swebench__a", "swebench__c"]


def test_a_capped_replay_stops_reading_at_the_next_session(bench):
    def rows():
        yield _row("gaia__a", 0)
        yield _row("gaia__a", 1)
        yield _row("gaia__b", 0)
        raise AssertionError("read past the cap")

    got = bench.group_sessions(rows(), None, 1)
    assert [len(s) for s in got] == [2]


def test_null_message_fields_are_dropped(bench):
    row = {
        "input": [
            {"role": "user", "content": "hi", "tool_calls": None, "name": None},
            {"role": "assistant", "content": "", "tool_call_id": None},
        ]
    }
    assert bench.messages(row) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""},
    ]


def test_failures_say_whether_the_context_was_outgrown(bench):
    err = "HTTP 400: This model's maximum context length is 32768 tokens."
    assert bench.failure_reason(err) == "context"
    err = (
        "HTTP 400: The decoder prompt (length 40000) is longer than the maximum "
        "model length of 32768."
    )
    assert bench.failure_reason(err) == "context"
    assert bench.failure_reason("HTTP 500: boom") == "error"


def test_percentile_is_nearest_rank(bench):
    assert bench.percentile([], 50) == 0.0
    assert bench.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0
    assert bench.percentile([1.0, 2.0], 99) == 2.0
    assert bench.percentile([1.0, 2.0, 3.0, 4.0], 25) == 1.0


def test_metrics_keep_the_cache_counters_summed_over_labels(bench):
    text = "\n".join(
        [
            "# HELP vllm:prefix_cache_hits_total hits",
            "# TYPE vllm:prefix_cache_hits_total counter",
            'vllm:prefix_cache_hits_total{engine="0"} 10',
            'vllm:prefix_cache_hits_total{engine="1"} 5',
            'vllm:prefix_cache_hits_created{engine="0"} 1.7e9',
            "vllm:prefix_cache_queries_total 20",
            "vllm:prefix_cache_queries_created 1.7e9",
            'vllm:num_requests_running{engine="0"} 3',
            "vllm:gpu_prefix_cache_hit_rate 0.75",
            'lmcache:retrieve_speed_bucket{le="1.0"} 4',
            'lmcache:retrieve_speed_bucket{le="+Inf"} 9',
            "lmcache:retrieve_speed_count 9",
            "lmcache:retrieve_speed_sum 3.5",
            "lmcache:num_hit_tokens_total 7",
        ]
    )
    assert bench.parse_metrics(text) == {
        "vllm:prefix_cache_hits_total": 15.0,
        "vllm:prefix_cache_queries_total": 20.0,
        "lmcache:num_hit_tokens_total": 7.0,
    }


def test_a_replay_needs_a_worker(bench, monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["kvcache-bench", "--preset", "gaia-burst", "--output", "x"]
    )
    monkeypatch.setenv("KVBENCH_WORKERS", "0")
    with pytest.raises(SystemExit) as exc:
        bench.main()
    assert exc.value.code == 2


def test_metrics_deltas_are_null_unless_both_reads_succeeded(bench):
    assert bench.metrics_delta({"a_total": 1.0}, {"a_total": 4.0}) == {"a_total": 3.0}
    assert bench.metrics_delta(None, {"a_total": 4.0}) is None
    assert bench.metrics_delta({"a_total": 1.0}, None) is None


def test_a_failed_metrics_read_is_named_not_parsed(bench):
    http = _HTTP({"/metrics": [_Response(503, body="lmcache:x_total 9")]})
    got = asyncio.run(bench.scrape_metrics(http, "http://127.0.0.1"))
    assert got == (None, "HTTP 503")


def test_a_streamed_turn_counts_its_usage(bench):
    got = _turn(
        bench,
        ": keep-alive",
        *_sse(
            {"choices": [{"delta": {"content": "x"}}]},
            {"usage": {"prompt_tokens": 12, "completion_tokens": 3}},
            "[DONE]",
        ),
    )
    assert (got["prompt_tokens"], got["completion_tokens"]) == (12, 3)
    assert 0 <= got["ttft"] <= got["latency"]


def test_a_stream_done_before_its_first_chunk_fails(bench):
    with pytest.raises(RuntimeError, match="before its first chunk"):
        _turn(bench, *_sse("[DONE]", {"usage": {"prompt_tokens": 1}}))


def test_an_error_chunk_fails_the_turn_with_its_message(bench):
    with pytest.raises(RuntimeError, match="^boom$"):
        _turn(bench, *_sse({"error": {"message": "boom"}}))


def test_a_refused_request_fails_with_its_status(bench):
    with pytest.raises(RuntimeError, match="^HTTP 400: too long$"):
        _turn(bench, status=400, body="too long")


@pytest.mark.parametrize(
    "chunk",
    ["[1, 2]", "not json", {"usage": {"prompt_tokens": "many"}}, {"usage": [1]}],
)
def test_a_malformed_chunk_is_a_protocol_error(bench, chunk):
    with pytest.raises(bench.ProtocolError):
        _turn(bench, *_sse(chunk))


def test_a_null_token_count_reads_as_zero(bench):
    got = _turn(bench, *_sse({"usage": {"prompt_tokens": None}}))
    assert got["prompt_tokens"] == 0


def test_a_run_summarises_completed_and_failed_sessions(bench, monkeypatch, tmp_path):
    rows = [
        _row("gaia__ok", 0),
        _row("gaia__ok", 1),
        _row("gaia__long", 0),
        _row("gaia__long", 1),
        _row("gaia__bad", 0),
    ]
    outcomes = {
        ("gaia__long", 1): RuntimeError("HTTP 400: maximum context length is 8"),
        ("gaia__bad", 0): bench.ProtocolError("chunk is not an object: []"),
    }

    async def replay_turn(http, endpoint, model, row, cap):
        key = (row["session_id"], row["t"])
        if key in outcomes:
            raise outcomes[key]
        return {"ttft": 0.1, "latency": 0.2, "prompt_tokens": 5, "completion_tokens": 2}

    http = _HTTP(
        {
            "/v1/models": [_Response(body='{"data": [{"id": "m"}]}')],
            "/metrics": [
                _Response(body="lmcache:hits_total 1"),
                _Response(body="lmcache:hits_total 4"),
            ],
        }
    )
    monkeypatch.setattr(bench, "load_rows", lambda *a, **k: rows)
    monkeypatch.setattr(bench, "replay_turn", replay_turn)
    monkeypatch.setattr(bench.aiohttp, "ClientSession", lambda **k: http, raising=False)
    args = argparse.Namespace(
        preset="gaia-burst",
        dataset="d",
        dataset_revision="abc123",
        sessions=0,
        endpoint="http://127.0.0.1/",
        model="",
        workers=2,
        max_tokens=0,
        output=str(tmp_path),
    )
    assert asyncio.run(bench.run(args)) == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["dataset_revision"] == "abc123"
    assert summary["sessions"] == 3
    assert summary["sessions_failed"] == 2
    assert summary["failure_rate"] == round(2 / 3, 4)
    assert summary["failures_by_reason"] == {"context": 1, "protocol": 1}
    assert (summary["turns"], summary["turns_completed"]) == (5, 3)
    assert summary["prompt_tokens"] == 15
    assert summary["cache_metrics_delta"] == {"lmcache:hits_total": 3.0}
    assert summary["metrics_errors"] == {}
    turns = json.loads((tmp_path / "turns.json").read_text())
    assert [(t["session_id"], t["turn"]) for t in turns] == [
        ("gaia__ok", 0),
        ("gaia__ok", 1),
        ("gaia__long", 0),
    ]


def test_a_failed_metrics_read_publishes_no_delta(bench, monkeypatch, tmp_path):
    async def replay_turn(http, endpoint, model, row, cap):
        return {"ttft": 0.1, "latency": 0.2, "prompt_tokens": 5, "completion_tokens": 2}

    http = _HTTP(
        {
            "/metrics": [
                _Response(404, body="not found"),
                _Response(body="lmcache:hits_total 4"),
            ]
        }
    )
    monkeypatch.setattr(bench, "load_rows", lambda *a, **k: [_row("gaia__a")])
    monkeypatch.setattr(bench, "replay_turn", replay_turn)
    monkeypatch.setattr(bench.aiohttp, "ClientSession", lambda **k: http, raising=False)
    args = argparse.Namespace(
        preset="gaia-burst",
        dataset="d",
        dataset_revision="",
        sessions=0,
        endpoint="http://127.0.0.1",
        model="m",
        workers=1,
        max_tokens=0,
        output=str(tmp_path),
    )
    assert asyncio.run(bench.run(args)) == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["dataset_revision"] is None
    assert summary["cache_metrics_delta"] is None
    assert summary["metrics_errors"] == {"before": "HTTP 404"}
