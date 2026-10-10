# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the kvcache suite's wait and stop steps (`f/kvcache`)."""

import pytest

from f.kvcache import common, stop, wait

UNIT = "kvcache@gaia-burst.service"
RAN = {"ActiveState": "inactive", "Job": "", "Result": "success"}


@pytest.fixture
def workers(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    return tmp_path / "workers"


class Clock:
    """Stands in for the `time` module: every sleep advances the clock."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = 0

    def time(self):
        return 1_700_000_000 + self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds


class FakeRemote:
    """Answers the replay unit's polls in turn; an Exception is a failed poll."""

    def __init__(self, polls, servers=None):
        self._polls = list(polls)
        self._servers = servers or {}
        self.calls: list[tuple] = []

    def show(self, unit, *props):
        if unit != UNIT:
            return self._servers.get(unit, {})
        answer = self._polls.pop(0) if len(self._polls) > 1 else self._polls[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def systemctl(self, *args, **_):
        self.calls.append(("systemctl", *args))

    def ssh(self, *args, **_):
        self.calls.append(args)
        return ""

    def journal_combined(self, unit, cursor):
        return cursor, ""


class FakeHost:
    def __init__(self, states):
        self._states = list(states)

    def systemctl(self, *args, **_):
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]


def _wait(monkeypatch, remote, host_states=("active",), **kwargs):
    clock = Clock()
    monkeypatch.setattr(wait, "time", clock)
    monkeypatch.setattr(wait, "RemoteSystemd", lambda workers, vm: remote)
    monkeypatch.setattr(wait, "Systemd", lambda workers: FakeHost(host_states))
    out = wait.main("vm0", "gaia-burst", **kwargs)
    return out, clock


# --- the poll decision --------------------------------------------------------


@pytest.mark.parametrize(
    "host, state, errors, overdue, want",
    [
        ("active", {"ActiveState": "inactive", "Job": "7"}, 0, False, "poll"),
        ("active", {"ActiveState": "activating", "Job": ""}, 0, False, "poll"),
        ("active", {"ActiveState": "inactive", "Job": ""}, 0, False, "done"),
        ("active", {"ActiveState": "failed", "Job": ""}, 0, False, "done"),
        ("active", {"ActiveState": "active", "Job": ""}, 0, True, "timeout"),
        ("failed", {"ActiveState": "active", "Job": ""}, 0, False, "crashed"),
        ("inactive", None, 0, False, "crashed"),
        ("", None, 0, False, "crashed"),
        ("active", None, 19, False, "poll"),
        ("active", None, 19, True, "timeout"),
        ("active", None, 20, False, "crashed"),
    ],
)
def test_decide(host, state, errors, overdue, want):
    assert wait.decide(host, state, errors, overdue) == want


@pytest.mark.parametrize(
    "exited, before, want",
    [
        ("0", "", True),
        ("", "", True),
        ("500", "500", True),
        ("600", "500", False),
        ("600", "", False),
        ("600", "0", False),
    ],
)
def test_never_ran(exited, before, want):
    state = {**RAN, "InactiveExitTimestampMonotonic": exited}
    assert wait.never_ran(state, before) is want


# --- the wait loop ------------------------------------------------------------


def test_an_inactive_unit_with_a_queued_job_keeps_polling(workers, monkeypatch):
    queued = {"ActiveState": "inactive", "Job": "42", "Result": "success"}
    done = {**RAN, "InactiveExitTimestampMonotonic": "900"}
    remote = FakeRemote([queued, queued, {**done, "ActiveState": "active"}, done])
    out, clock = _wait(monkeypatch, remote, inactive_exit="100")
    assert clock.sleeps == 3
    assert (out["result"], out["never_ran"], out["crashed"]) == (
        "success",
        False,
        False,
    )


def test_the_transport_budget_is_twenty_failed_polls(workers, monkeypatch):
    remote = FakeRemote([RuntimeError("ssh exited 255")])
    out, clock = _wait(monkeypatch, remote)
    assert out["crashed"] and not out["timed_out"]
    assert clock.sleeps == wait._TRANSPORT_BUDGET - 1 == 19


def test_a_good_poll_resets_the_transport_budget(workers, monkeypatch):
    err = RuntimeError("ssh exited 255")
    busy = {"ActiveState": "active", "Job": ""}
    done = {**RAN, "InactiveExitTimestampMonotonic": "900"}
    remote = FakeRemote([err] * 19 + [busy] + [err] * 19 + [done])
    out, _ = _wait(monkeypatch, remote)
    assert not out["crashed"]


def test_a_dead_qemu_is_a_crash(workers, monkeypatch):
    remote = FakeRemote([{"ActiveState": "active", "Job": ""}])
    out, clock = _wait(monkeypatch, remote, host_states=("active", "failed"))
    assert out["crashed"] and clock.sleeps == 1
    assert out["never_ran"] is False


def test_a_replay_past_the_timeout_is_stopped(workers, monkeypatch):
    remote = FakeRemote([{"ActiveState": "active", "Job": ""}])
    out, _ = _wait(monkeypatch, remote, timeout=60, poll_interval=15)
    assert out["timed_out"] and not out["crashed"]
    assert ("systemctl", "stop", UNIT) in remote.calls


def test_a_replay_whose_server_never_came_up_never_ran(workers, monkeypatch):
    # vllm-serve died while the replay's start job was queued: systemd dropped
    # the job, so the unit is inactive, Result=success from reset-failed, and
    # its inactive-exit timestamp is the one start read.
    dropped = {**RAN, "ExecMainStatus": "0", "InactiveExitTimestampMonotonic": "500"}
    servers = {
        "vllm-serve.service": {"ActiveState": "failed", "Result": "exit-code"},
        "vllm-ready.service": {"ActiveState": "inactive", "Result": "success"},
    }
    remote = FakeRemote([dropped], servers)
    out, _ = _wait(monkeypatch, remote, inactive_exit="500")
    assert out["never_ran"] is True
    assert "vllm-serve.service failed (Result=exit-code)" in out["reason"]
    assert any("_SYSTEMD_UNIT=vllm-serve.service" in c for c in remote.calls)
    unit_ok = out["result"] == "success" and not out["never_ran"]
    assert common.item_status(None, unit_ok, False, False, 0.05) == "failed"


# --- stop ---------------------------------------------------------------------


def test_stop_never_raises(workers, monkeypatch):
    class Broken:
        def __init__(self, *_):
            raise RuntimeError("no vsock CID")

    monkeypatch.setattr(stop, "RemoteSystemd", Broken)
    out = stop.main("vm0", ["gaia-burst"])
    assert out["skipped_no_transport"] is True

    class Flaky(FakeRemote):
        def systemctl(self, *args, **_):
            raise RuntimeError("ssh exited 255")

    monkeypatch.setattr(stop, "RemoteSystemd", lambda workers, vm: Flaky([RAN]))
    assert stop.main("vm0", ["gaia-burst"])["stopped"][-1] == "vllm-serve.service"
    assert stop.main("")["stopped"] == []


def test_stop_stops_the_replays_then_the_server_and_clears_the_disk(
    workers, monkeypatch
):
    disk = common.lmcache_disk_dir("vm0")
    disk.mkdir(parents=True)
    remote = FakeRemote([RAN])
    monkeypatch.setattr(stop, "RemoteSystemd", lambda workers, vm: remote)
    stop.main("vm0", ["gaia-burst"])
    assert [c[1:] for c in remote.calls if c[1] == "stop"] == [
        ("stop", UNIT),
        ("stop", "vllm-serve.service"),
    ]
    assert not disk.exists()
