# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the kvcache suite's host side (`f/kvcache`)."""

import ast
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from f.kvcache import collect, common, discover, judge, render_config, report, start

REPO = Path(__file__).resolve().parent.parent
FLOW = REPO / "f/kvcache/run.flow/flow.yaml"
STEP = REPO / "f/kvcache/render_config.script.yaml"
BENCH = REPO / "vendor/nixos-flake/pkgs/kvcache-bench/kvcache-bench.py"
KVER = "7.3.0-rc6-demo"


@pytest.fixture
def workers(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    return tmp_path / "workers"


def _summary(**over):
    s = {
        "preset": "swebench-realistic",
        "model": "Qwen/Qwen3-8B",
        "sessions": 8,
        "sessions_failed": 0,
        "failure_rate": 0.0,
        "failures_by_reason": {},
        "turns_completed": 40,
        "wall_seconds": 12.5,
        "ttft_p50": 0.1,
        "ttft_p95": 0.4,
        "ttft_p99": 0.9,
        "latency_p50": 1.0,
        "latency_p95": 2.0,
        "cache_metrics_delta": {
            "vllm:prefix_cache_hits_total": 750.0,
            "vllm:prefix_cache_queries_total": 1000.0,
        },
    }
    s.update(over)
    return s


# --- the catalogs -------------------------------------------------------------


def test_the_preset_fallback_is_the_replayers_own_list():
    tree = ast.parse(BENCH.read_text())
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "PRESETS"
    )
    assert node.value is not None
    shipped = {k: v[2] for k, v in ast.literal_eval(node.value).items()}
    assert common.PRESETS == shipped
    assert set(common.DEFAULT_PRESETS) <= set(common.PRESETS)


def test_the_models_are_unique_and_the_default_is_one_of_them():
    ids = [m["id"] for m in common.MODELS]
    assert len(ids) == len(set(ids))
    assert common.DEFAULT_MODEL in ids
    assert all(m["context"] >= 32768 for m in common.MODELS)


def test_every_curated_model_and_the_dataset_are_pinned_to_a_commit():
    for sha in [m["revision"] for m in common.MODELS] + [
        common.DEFAULT_DATASET_REVISION
    ]:
        assert len(sha) == 40 and int(sha, 16) >= 0
    assert common.model_revision("Qwen/Qwen3-8B") == (
        "b968826d9c46dd6066d109eabc6255188de91218"
    )
    assert common.model_revision("someone/custom-model") == ""


def _fallback(name: str):
    code = yaml.safe_load(FLOW.read_text())["schema"]["x-windmill-dyn-select-code"]
    node = next(
        n
        for n in ast.parse(code).body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == name
    )
    return ast.literal_eval(node.value)


def test_the_form_defaults_and_choices_are_the_common_ones():
    flow = yaml.safe_load(FLOW.read_text())["schema"]["properties"]
    step = yaml.safe_load(STEP.read_text())["schema"]["properties"]
    engine = flow["engine"]["properties"]
    cache = flow["cache"]["properties"]
    replay = flow["replay"]["properties"]
    pairs = [
        (flow["presets"], step["presets"], common.DEFAULT_PRESETS),
        (engine["model"], step["model"], common.DEFAULT_MODEL),
        (cache["backend"], step["backend"], common.DEFAULT_BACKEND),
        (cache["cpu_size_gb"], step["cpu_size_gb"], common.DEFAULT_CPU_SIZE_GB),
        (cache["disk_size_gb"], step["disk_size_gb"], common.DEFAULT_DISK_SIZE_GB),
        (replay["sessions"], step["sessions"], common.DEFAULT_SESSIONS),
        (replay["max_tokens"], step["max_tokens"], common.DEFAULT_MAX_TOKENS),
        (replay["dataset"], step["dataset"], common.DEFAULT_DATASET),
        (
            replay["dataset_revision"],
            step["dataset_revision"],
            common.DEFAULT_DATASET_REVISION,
        ),
        (
            engine["gpu_memory_utilization"],
            step["gpu_memory_utilization"],
            common.DEFAULT_GPU_MEMORY_UTILIZATION,
        ),
    ]
    for in_flow, in_step, want in pairs:
        assert in_flow["default"] == in_step["default"] == want
    assert cache["backend"]["enum"] == step["backend"]["enum"] == list(common.BACKENDS)
    assert _fallback("_PRESETS") == common.PRESETS
    assert [
        {"value": v, "label": label} for v, label in _fallback("_MODELS")
    ] == common.list_models()


def test_list_models_labels_size_and_context():
    labels = [o["label"] for o in common.list_models("qwen3-8b")]
    assert labels == ["Qwen/Qwen3-8B (8.2B, 40K context)"]


def test_list_presets_falls_back_then_reads_discovers_cache(workers):
    assert {o["value"] for o in common.list_presets("vm0")} == set(common.PRESETS)
    cache = common.presets_cache("vm0")
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"only-this": "one preset"}))
    assert common.list_presets("vm0") == [
        {"value": "only-this", "label": "only-this: one preset"}
    ]


# --- the share ----------------------------------------------------------------


@pytest.mark.parametrize(
    "vm, kver, preset",
    [
        ("../x", KVER, "p"),
        ("vm0", "../../x", "p"),
        ("vm0", KVER, "../x"),
        ("vm0", KVER, ""),
        ("vm0", KVER, "gaia burst"),
        ("vm0", KVER, "gaia\\burst"),
        ("vm0", "7.3 rc6", "p"),
    ],
)
def test_paths_refuse_to_leave_the_share(workers, vm, kver, preset):
    with pytest.raises(ValueError):
        common.results_dir(vm, kver, preset)


def test_results_and_report_are_keyed_by_kernel(workers):
    share = workers / "shared/kvcache/vm0"
    assert common.results_dir("vm0", KVER, "gaia-burst") == (
        share.resolve() / KVER / "results/gaia-burst"
    )
    assert common.report_path("vm0", KVER) == share.resolve() / KVER / "report.json"
    assert common.report_path("vm0") == share.resolve() / "report.json"


def test_env_file_quotes_for_systemd_and_refuses_newlines():
    body = common.env_file({"A": 'say "hi"', "B": "c:\\d", "C": 4})
    assert body == 'A="say \\"hi\\""\nB="c:\\\\d"\nC="4"\n'
    with pytest.raises(ValueError, match="newline"):
        common.env_file({"A": "x\ny"})


# --- the configuration --------------------------------------------------------


def test_serve_config_wires_lmcache_unless_the_backend_is_none():
    cfg = common.serve_config("m", backend="cpu")
    assert json.loads(cfg["kv-transfer-config"]) == {
        "kv_connector": "LMCacheConnectorV1",
        "kv_role": "kv_both",
    }
    assert "max-model-len" not in cfg
    none = common.serve_config("m", max_model_len=8192, backend="none")
    assert "kv-transfer-config" not in none
    assert none["max-model-len"] == 8192
    # The unit pins the address on its command line.
    assert not {"host", "port"} & (set(cfg) | set(none))


def test_serve_config_pins_the_revision():
    assert common.serve_config("m", revision="abc")["revision"] == "abc"
    assert "revision" not in common.serve_config("m")


def test_serve_config_advanced_arguments_replace_the_form():
    cfg = common.serve_config(
        "m",
        max_model_len=8192,
        backend="cpu",
        extra={"--tensor-parallel-size": 2, "dtype": "half"},
        revision="abc",
    )
    assert set(cfg) == {
        "model",
        "revision",
        "tensor-parallel-size",
        "dtype",
        "kv-transfer-config",
    }
    assert (cfg["tensor-parallel-size"], cfg["dtype"]) == (2, "half")
    reserved = (
        "port",
        "--host",
        "model",
        "config",
        "served-model-name",
        "served_model_name",
        "kv-transfer-config",
        "--kv_transfer_config",
    )
    for key in reserved:
        with pytest.raises(ValueError, match="not overridable"):
            common.serve_config("m", extra={key: "x"})
    with pytest.raises(ValueError, match="not overridable"):
        common.serve_config("m", extra={"revision": "x"}, revision="abc")
    assert common.serve_config("m", extra={"revision": "x"})["revision"] == "x"


def test_lmcache_config_per_backend():
    assert common.lmcache_config("cpu", 10)["max_local_cpu_size"] == 10.0
    disk = common.lmcache_config("disk", 10, 50)
    assert disk["local_disk"] == "file:///var/lib/kvcache/lmcache-disk/"
    assert disk["max_local_disk_size"] == 50.0
    with pytest.raises(ValueError, match="disk_size_gb"):
        common.lmcache_config("disk", 10, 0)
    with pytest.raises(ValueError, match="unknown backend"):
        common.lmcache_config("nvme", 10)
    with pytest.raises(ValueError, match="cpu_size_gb"):
        common.lmcache_config("cpu", 0)
    with pytest.raises(ValueError, match="offload backend"):
        common.lmcache_config("none", extra={"chunk_size": 512})


def test_lmcache_advanced_keys_replace_the_form_but_keep_the_disk_path():
    assert common.lmcache_config("cpu", 10, extra={"chunk_size": 512}) == {
        "chunk_size": 512
    }
    disk = common.lmcache_config("disk", 0, 0, extra={"max_local_disk_size": 5})
    assert disk == {
        "max_local_disk_size": 5,
        "local_disk": "file:///var/lib/kvcache/lmcache-disk/",
    }
    with pytest.raises(ValueError, match="not overridable"):
        common.lmcache_config("disk", extra={"local_disk": "file:///tmp/x/"})


def test_render_config_writes_the_four_files(workers):
    out = render_config.main(
        "vm0",
        ["gaia-burst"],
        device="cuda",
        model="Qwen/Qwen3-8B",
        backend="cpu",
        sessions=2,
        lmcache_args={"chunk_size": 512},
    )
    assert out["presets"] == ["gaia-burst"]
    share = workers / "shared/kvcache/vm0"
    serve = json.loads((share / "vllm-serve.yaml").read_text())
    assert serve["model"] == "Qwen/Qwen3-8B"
    assert (share / "vllm-serve.env").read_text() == (
        'LMCACHE_CONFIG_FILE="/var/lib/kvcache/lmcache.yaml"\n'
    )
    assert serve["revision"] == common.model_revision("Qwen/Qwen3-8B")
    assert json.loads((share / "lmcache.yaml").read_text()) == {"chunk_size": 512}
    bench = (share / "kvcache.env").read_text()
    assert 'KVBENCH_SESSIONS="2"' in bench
    assert f'KVBENCH_DATASET_REVISION="{common.DEFAULT_DATASET_REVISION}"' in bench


def test_render_config_pins_no_revision_it_does_not_know(workers):
    render_config.main(
        "vm0", ["gaia-burst"], model="someone/custom-model", dataset_revision=""
    )
    share = workers / "shared/kvcache/vm0"
    assert "revision" not in json.loads((share / "vllm-serve.yaml").read_text())
    assert "KVBENCH_DATASET_REVISION" not in (share / "kvcache.env").read_text()


@pytest.mark.parametrize(
    "field, value",
    [
        ("sessions", -1),
        ("workers", 0),
        ("max_tokens", -1),
        ("gpu_memory_utilization", 0),
        ("gpu_memory_utilization", 1.5),
        ("max_model_len", -1),
    ],
)
def test_render_config_refuses_out_of_range_settings(workers, field, value):
    with pytest.raises(ValueError, match=field):
        render_config.main("vm0", ["gaia-burst"], **{field: value})
    assert not (workers / "shared/kvcache/vm0").exists()


@pytest.mark.parametrize(
    "backend, field", [("cpu", "cpu_size_gb"), ("disk", "disk_size_gb")]
)
def test_render_config_refuses_an_empty_offload(workers, backend, field):
    size: dict[str, Any] = {field: 0}
    with pytest.raises(ValueError, match=field):
        render_config.main(
            "vm0", ["gaia-burst"], device="cuda", backend=backend, **size
        )


def test_render_config_takes_zero_for_all_sessions_and_recorded_lengths(workers):
    render_config.main("vm0", ["gaia-burst"], sessions=0, max_tokens=0)
    bench = (workers / "shared/kvcache/vm0/kvcache.env").read_text()
    assert 'KVBENCH_SESSIONS="0"' in bench
    assert 'KVBENCH_MAX_TOKENS="0"' in bench


def test_render_config_checks_no_engine_field_an_override_replaces(workers):
    render_config.main(
        "vm0",
        ["gaia-burst"],
        gpu_memory_utilization=0,
        serve_args={"gpu-memory-utilization": 0.9},
    )
    serve = json.loads((workers / "shared/kvcache/vm0/vllm-serve.yaml").read_text())
    assert serve["gpu-memory-utilization"] == 0.9


def test_render_config_defaults_are_the_cpu_setup(workers):
    out = render_config.main("vm0", common.DEFAULT_PRESETS)
    share = workers / "shared/kvcache/vm0"
    serve = json.loads((share / "vllm-serve.yaml").read_text())
    assert out["backend"] == "none"
    assert serve["model"] == common.DEFAULT_MODEL
    assert serve["gpu-memory-utilization"] == 0.5
    bench = (share / "kvcache.env").read_text()
    assert 'KVBENCH_SESSIONS="1"' in bench
    assert 'KVBENCH_MAX_TOKENS="8"' in bench
    assert not (share / "lmcache.yaml").exists()


def test_render_config_writes_nothing_when_a_setting_is_refused(workers):
    with pytest.raises(ValueError, match="disk_size_gb"):
        render_config.main(
            "vm0", ["gaia-burst"], device="cuda", backend="disk", disk_size_gb=0
        )
    with pytest.raises(ValueError, match="not overridable"):
        render_config.main("vm0", ["gaia-burst"], serve_args={"port": 9000})
    assert not (workers / "shared/kvcache/vm0").exists()


def test_render_config_refuses_an_offload_on_a_cpu_engine(workers):
    with pytest.raises(ValueError, match="no connector"):
        render_config.main("vm0", ["gaia-burst"], device="cpu", backend="cpu")


def test_render_config_serves_the_baseline_on_a_cpu_engine(workers):
    render_config.main("vm0", ["gaia-burst"], device="cpu", backend="none")
    share = workers / "shared/kvcache/vm0"
    assert "kv-transfer-config" not in json.loads(
        (share / "vllm-serve.yaml").read_text()
    )
    assert (share / "vllm-serve.env").read_text() == (
        'VLLM_CPU_OMP_THREADS_BIND="nobind"\n'
    )


def test_render_config_refuses_a_cpu_engine_without_numa(workers):
    with pytest.raises(ValueError, match="NUMA"):
        render_config.main(
            "vm0", ["gaia-burst"], device="cpu", numa=False, backend="none"
        )


@pytest.mark.parametrize(
    "presets, needle", [([], "at least one"), (["nope"], "unknown preset")]
)
def test_render_config_refuses_bad_presets(workers, presets, needle):
    with pytest.raises(ValueError, match=needle):
        render_config.main("vm0", presets, device="cuda")


# --- the verdict --------------------------------------------------------------


@pytest.mark.parametrize(
    "summary, unit_ok, crashed, timed_out, want",
    [
        (_summary(), True, False, False, "passed"),
        (_summary(), True, True, False, "failed"),
        (_summary(), True, False, True, "failed"),
        (_summary(), False, False, False, "failed"),
        (None, True, False, False, "notrun"),
        (_summary(turns_completed=0), True, False, False, "notrun"),
        (
            _summary(sessions=4, sessions_failed=1, failures_by_reason={"error": 1}),
            True,
            False,
            False,
            "failed",
        ),
        (
            _summary(sessions=20, sessions_failed=1, failures_by_reason={"error": 1}),
            True,
            False,
            False,
            "passed",
        ),
        # The live CPU run: its one session outgrew the 32768-token context.
        (
            _summary(
                sessions=1,
                sessions_failed=1,
                failure_rate=1.0,
                failures_by_reason={"context": 1},
                turns_completed=6,
            ),
            True,
            False,
            False,
            "passed",
        ),
        (
            _summary(
                sessions=2,
                sessions_failed=2,
                failures_by_reason={"context": 1, "error": 1},
            ),
            True,
            False,
            False,
            "failed",
        ),
        (
            _summary(sessions=1, sessions_failed=1, failures_by_reason={"context": 1}),
            True,
            True,
            False,
            "failed",
        ),
    ],
)
def test_item_status(summary, unit_ok, crashed, timed_out, want):
    assert common.item_status(summary, unit_ok, crashed, timed_out, 0.05) == want


def test_run_status_never_passes_nothing():
    assert common.run_status([]) == "failed"
    assert common.run_status([{"status": "passed"}, "error object"]) == "failed"
    assert common.run_status([{"status": "passed"}]) == "passed"


def test_collect_reads_the_summary_and_gates_it(workers):
    path = common.results_dir("vm0", KVER, "gaia-burst") / "summary.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_summary()))
    out = collect.main("vm0", "gaia-burst", KVER, unit_ok=True)
    assert out["status"] == "passed"
    assert out["summary"]["ttft_p95"] == 0.4
    crashed = collect.main("vm0", "gaia-burst", KVER, unit_ok=True, crashed=True)
    assert crashed["status"] == "failed"


def test_collect_without_a_summary_is_notrun(workers):
    assert collect.main("vm0", "gaia-burst", KVER, unit_ok=True)["status"] == "notrun"


def test_collect_fails_a_replay_that_never_ran_with_the_reason(workers):
    out = collect.main("vm0", "gaia-burst", KVER, unit_ok=False, reason="no server")
    assert (out["status"], out["reason"]) == ("failed", "no server")
    with pytest.raises(RuntimeError, match="no server"):
        judge.main([out])


@pytest.mark.parametrize("rate", [-0.1, 1.5])
def test_collect_refuses_a_failure_rate_outside_zero_to_one(workers, rate):
    with pytest.raises(ValueError, match="max_failure_rate"):
        collect.main("vm0", "gaia-burst", KVER, max_failure_rate=rate)


def test_report_renders_three_tables_and_writes_the_rollup(workers):
    item = {
        "preset": "gaia-burst",
        "status": "passed",
        "kernel_version": KVER,
        "summary": _summary(),
    }
    out = report.main([item], vm_name="vm0")
    assert list(out) == ["render_all"]
    run_info, presets, counters = out["render_all"]
    assert run_info[0]["model"] == "Qwen/Qwen3-8B"
    assert presets[0]["prefix hit %"] == 75.0
    assert {c["counter"] for c in counters} == {
        "vllm:prefix_cache_hits_total",
        "vllm:prefix_cache_queries_total",
    }
    rollup = json.loads(common.report_path("vm0", KVER).read_text())
    assert rollup["status"] == "passed"


def test_report_of_nothing_renders_and_fails(workers):
    out = report.main([], vm_name="vm0")
    assert list(out) == ["render_all"]
    rollup = json.loads(common.report_path("vm0").read_text())
    assert rollup["status"] == "failed"


def test_judge_fails_red_and_passes_green_through():
    with pytest.raises(RuntimeError, match="not passed"):
        judge.main([{"preset": "p", "status": "failed"}])
    with pytest.raises(RuntimeError, match="no preset ran"):
        judge.main([])
    green = {"render_all": [{"markdown": "x"}]}
    assert judge.main([{"preset": "p", "status": "passed"}], green) is green


# --- the guest steps, over a fake transport -----------------------------------


class FakeRemote:
    """Answers the guest commands the steps issue, keyed by the command name."""

    def __init__(self, ssh: dict | None = None, show: dict | None = None):
        self._ssh = {
            "mountpoint": 0,
            "kvcache-bench": json.dumps(common.PRESETS),
            "nvidia-smi": subprocess.CalledProcessError(127, "nvidia-smi"),
            "test": 0,
            "grep": 0,
            "cat": KVER + "\n",
            **(ssh or {}),
        }
        self._show = show or {}
        self.calls: list[tuple] = []

    def is_system_running(self) -> str:
        return "running"

    def unit_exists(self, unit: str) -> bool:
        return True

    def ssh(self, *args, capture=True, check=True, **_):
        self.calls.append(args)
        answer = self._ssh[args[0]]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def systemctl(self, *args, **_):
        self.calls.append(("systemctl", *args))

    def show(self, unit, *props):
        return self._show


def _discover(monkeypatch, remote):
    monkeypatch.setattr(discover, "RemoteSystemd", lambda workers, vm: remote)
    return discover.main("vm0")


@pytest.mark.parametrize(
    "answer, device, gpu",
    [
        ("NVIDIA L4\n", "cuda", "NVIDIA L4"),
        # Driver not loaded: the error goes to stdout and the exit status is 9.
        (
            subprocess.CalledProcessError(
                9, "nvidia-smi", output="NVIDIA-SMI has failed"
            ),
            "cpu",
            None,
        ),
        (subprocess.CalledProcessError(127, "nvidia-smi"), "cpu", None),
    ],
)
def test_discover_takes_the_gpu_only_from_a_successful_nvidia_smi(
    workers, monkeypatch, answer, device, gpu
):
    out = _discover(monkeypatch, FakeRemote({"nvidia-smi": answer}))
    assert (out["device"], out["gpu"]) == (device, gpu)
    assert out["kernel_version"] == KVER


@pytest.mark.parametrize(
    "node, mems, numa", [(0, 0, True), (1, 0, False), (0, 1, False)]
)
def test_discover_needs_a_node_and_its_memory_nodes_for_numa(
    workers, monkeypatch, node, mems, numa
):
    out = _discover(monkeypatch, FakeRemote({"test": node, "grep": mems}))
    assert out["numa"] is numa


def test_discover_refuses_an_unmounted_share(workers, monkeypatch):
    with pytest.raises(RuntimeError, match="not the mounted kvcache share"):
        _discover(monkeypatch, FakeRemote({"mountpoint": 32}))


def _start(monkeypatch, remote):
    monkeypatch.setattr(start, "RemoteSystemd", lambda workers, vm: remote)
    return start.main("vm0", "gaia-burst", KVER)


def test_start_restarts_the_server_and_counts_a_queued_job_as_taken(
    workers, monkeypatch
):
    stale = common.results_dir("vm0", KVER, "gaia-burst")
    stale.mkdir(parents=True)
    (stale / "summary.json").write_text("{}")
    disk = common.lmcache_disk_dir("vm0")
    disk.mkdir(parents=True)
    remote = FakeRemote(
        show={
            "ActiveState": "inactive",
            "Job": "42",
            "InactiveExitTimestampMonotonic": "123",
        }
    )
    out = _start(monkeypatch, remote)
    assert (out["active_state"], out["inactive_exit"]) == ("inactive", "123")
    assert not stale.exists()
    assert not disk.exists()
    unit = "kvcache@gaia-burst.service"
    assert remote.calls == [
        ("systemctl", "stop", "vllm-serve.service"),
        ("systemctl", "reset-failed", "vllm-serve.service", "vllm-ready.service", unit),
        ("systemctl", "start", "--no-block", unit),
    ]


@pytest.mark.parametrize(
    "props, taken",
    [
        ({"ActiveState": "activating", "Job": ""}, True),
        ({"ActiveState": "active", "Job": ""}, True),
        ({"ActiveState": "inactive", "Job": ""}, False),
        ({"ActiveState": "failed", "Job": ""}, False),
    ],
)
def test_start_fails_a_start_that_did_not_take(workers, monkeypatch, props, taken):
    remote = FakeRemote(show=props)
    if taken:
        _start(monkeypatch, remote)
    else:
        with pytest.raises(RuntimeError, match="did not start"):
            _start(monkeypatch, remote)


# --- the flow -----------------------------------------------------------------


def test_the_flow_tears_down_on_success_and_on_failure():
    flow = yaml.safe_load(FLOW.read_text())["value"]
    ids = [m["id"] for m in flow["modules"]]
    assert ids.index("teardown") == ids.index("presets") + 1
    teardown = flow["modules"][ids.index("teardown")]["value"]
    assert teardown["path"] == flow["failure_module"]["value"]["path"]
    assert teardown["path"] == "f/kvcache/stop"


def test_the_flow_lists_vms_through_the_shared_module(monkeypatch):
    code = yaml.safe_load(FLOW.read_text())["schema"]["x-windmill-dyn-select-code"]
    scope: dict = {}
    exec(compile(code, "dyn-select", "exec"), scope)
    monkeypatch.delenv("WORKERS_DIR", raising=False)
    assert scope["list_vms"]() == []
