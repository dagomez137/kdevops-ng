# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the Grafana annotation step's pure paths (`f.monitoring.annotate`)."""

from f.monitoring import annotate

WM_ENV = ("BASE_INTERNAL_URL", "WM_TOKEN", "WM_WORKSPACE")
ENV_REASON = "Windmill job env incomplete (BASE_INTERNAL_URL/WM_TOKEN/WM_WORKSPACE)"


def _clear_wm_env(monkeypatch):
    for name in WM_ENV:
        monkeypatch.delenv(name, raising=False)


def test_run_window_spans_min_start_to_max_end():
    items = [
        {"started_realtime_ms": 5000.5, "ended_realtime_ms": 9000.9},
        {"started_realtime_ms": 3000, "ended_realtime_ms": 7000},
        "junk",
        {"started_realtime_ms": None, "ended_realtime_ms": None},
    ]
    assert annotate.run_window(items) == (3000, 9000)


def test_run_window_needs_both_edges():
    assert annotate.run_window([]) == (None, None)
    assert annotate.run_window([{"started_realtime_ms": 1}]) == (None, None)
    assert annotate.run_window([{"ended_realtime_ms": 2}]) == (None, None)


def test_run_verdict_passes_only_a_fully_green_run():
    assert annotate.run_verdict([]) == "failed"
    assert annotate.run_verdict([{"status": "passed"}]) == "passed"
    assert annotate.run_verdict([{"status": "passed"}, {"status": "failed"}]) == (
        "failed"
    )
    assert annotate.run_verdict(["passed"]) == "failed"


def test_incomplete_job_env_yields_a_skip_reason(monkeypatch):
    _clear_wm_env(monkeypatch)
    assert annotate.fetch_grafana_resource() == ENV_REASON


def test_main_without_a_window_skips():
    assert annotate.main(suite="fstests", per_item=[]) == {
        "posted": False,
        "reason": "no run window",
    }
    assert annotate.main(per_item=None) == {"posted": False, "reason": "no run window"}


def test_main_without_the_resource_skips_after_the_window(monkeypatch):
    _clear_wm_env(monkeypatch)
    out = annotate.main(
        suite="fstests",
        per_item=[
            {"started_realtime_ms": 1000, "ended_realtime_ms": 2000, "status": "passed"}
        ],
    )
    assert out == {"posted": False, "reason": ENV_REASON}
