# SPDX-License-Identifier: copyleft-next-0.3.1
"""Write a KV cache run's server and replay configuration onto the `kvcache` share.

Writes onto `$WORKERS_DIR/shared/kvcache/<vm>`, the host side of the guest's
/var/lib/kvcache, the four files the guest's units read: `vllm-serve.yaml`
(`vllm serve --config`), `vllm-serve.env` (the server's environment, pointing
LMCache at its config), `lmcache.yaml` (the offload backend) and `kvcache.env`
(the replay's settings). Changing the model, the backend or the replay needs no
closure rebuild. Every file is rendered and validated before the first is
written; each is then written atomically and echoed to the job log. The
advanced `serve_args` and `lmcache_args` objects, by `vllm serve` long option
name and LMCache key, each replace the form's settings in `vllm-serve.yaml`
and `lmcache.yaml` when set; the model, its revision, the LMCache wiring and
the disk path stay the form's. A curated model is served at its pinned
revision, and the replay reads the dataset at `dataset_revision`.

LMCache has no connector for a CPU engine: on one it logs so and recomputes
every prefix, which would measure nothing while looking configured. An offload
backend on a guest discover found no GPU on is refused; `none` serves with
vLLM's own prefix cache, the baseline, on any device. vLLM's CPU backend takes
its memory node from the kernel and fails without one, so a CPU engine on a
kernel without NUMA and cpusets is refused too. Returns the presets the
run's loop iterates. The host never contacts the guest.

Equivalent commands:

    cat > "$WORKERS_DIR/shared/kvcache/<vm>/vllm-serve.yaml"
    cat > "$WORKERS_DIR/shared/kvcache/<vm>/vllm-serve.env"
    cat > "$WORKERS_DIR/shared/kvcache/<vm>/lmcache.yaml"
    cat > "$WORKERS_DIR/shared/kvcache/<vm>/kvcache.env"
"""

from __future__ import annotations

import json

from f.kvcache.common import (
    BACKENDS,
    BENCH_ENV,
    DEFAULT_BACKEND,
    DEFAULT_CPU_SIZE_GB,
    DEFAULT_DATASET,
    DEFAULT_DATASET_REVISION,
    DEFAULT_DISK_SIZE_GB,
    DEFAULT_GPU_MEMORY_UTILIZATION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_SESSIONS,
    GUEST_STATE_DIR,
    LMCACHE_CONFIG,
    SERVE_CONFIG,
    SERVE_ENV,
    _atomic_write,
    env_file,
    lmcache_config,
    model_revision,
    serve_config,
    share_dir,
)
from f.kvcache.common import list_models as _list_models
from f.kvcache.common import list_presets as _list_presets
from f.kvcache.common import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def list_presets(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_presets` entrypoint: see `f.kvcache.common.list_presets`."""
    return _list_presets(vm_name, filterText)


def list_models(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_models` entrypoint: see `f.kvcache.common.list_models`."""
    return _list_models(filterText)


def check_replay(sessions: int, workers: int, max_tokens: int) -> None:
    """Refuse a replay setting out of range, naming the field; `sessions` and
    `max_tokens` take 0 for every session and the recorded length."""
    if int(sessions) < 0:
        raise ValueError(f"sessions must be 0 (every session) or more, got {sessions}")
    if int(workers) < 1:
        raise ValueError(f"workers must be 1 or more, got {workers}")
    if int(max_tokens) < 0:
        raise ValueError(
            f"max_tokens must be 0 (the recorded length) or more, got {max_tokens}"
        )


def check_engine(gpu_memory_utilization: float, max_model_len: int) -> None:
    """Refuse a form engine setting out of range, naming the field."""
    if not 0 < float(gpu_memory_utilization) <= 1:
        raise ValueError(
            "gpu_memory_utilization must be above 0 and at most 1, got "
            f"{gpu_memory_utilization}"
        )
    if int(max_model_len) < 0:
        raise ValueError(
            f"max_model_len must be 0 (the model's own) or more, got {max_model_len}"
        )


def _emit(path, text: str) -> None:
    _atomic_write(path, text)
    print(f"wrote {path}\n{text}", flush=True)


def main(
    vm_name: str,
    presets: list[str] | None = None,
    device: str = "cpu",
    numa: bool = True,
    model: str = DEFAULT_MODEL,
    max_model_len: int = 0,
    dtype: str = "auto",
    gpu_memory_utilization: float = DEFAULT_GPU_MEMORY_UTILIZATION,
    enable_prefix_caching: bool = True,
    backend: str = DEFAULT_BACKEND,
    cpu_size_gb: float = DEFAULT_CPU_SIZE_GB,
    disk_size_gb: float = DEFAULT_DISK_SIZE_GB,
    dataset: str = DEFAULT_DATASET,
    dataset_revision: str = DEFAULT_DATASET_REVISION,
    sessions: int = DEFAULT_SESSIONS,
    workers: int = 4,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    serve_args: dict | None = None,
    lmcache_args: dict | None = None,
) -> dict:
    presets = [p for p in (presets or []) if p]
    if not presets:
        raise ValueError("pick at least one replay preset")
    known = {o["value"] for o in _list_presets(vm_name)}
    unknown = [p for p in presets if p not in known]
    if unknown:
        raise ValueError(f"unknown preset(s) {unknown}: choose from {sorted(known)}")
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}: choose from {sorted(BACKENDS)}")
    if backend != "none" and device != "cuda":
        raise ValueError(
            f"backend {backend!r} offloads through LMCache, which has no connector "
            f"for a {device} engine and would recompute instead; pass a GPU through "
            "or pick backend none"
        )
    if device == "cpu" and not numa:
        raise ValueError(
            "vLLM's CPU backend reads the allowed NUMA memory nodes and the "
            "guest's kernel exposes none; build it with CONFIG_NUMA and "
            "CONFIG_CPUSETS (the imageless_defconfig preset, or the mem/numa and "
            "core/boot-config fragments)"
        )
    model = (model or "").strip()
    if not model:
        raise ValueError("pick a model")
    check_replay(sessions, workers, max_tokens)
    if not serve_args:
        check_engine(gpu_memory_utilization, max_model_len)

    serve = serve_config(
        model,
        max_model_len,
        dtype,
        gpu_memory_utilization,
        enable_prefix_caching,
        backend,
        serve_args,
        model_revision(model),
    )
    lmcache = lmcache_config(backend, cpu_size_gb, disk_size_gb, lmcache_args)
    serve_env = {}
    # vLLM's CPU backend binds its threads per NUMA node and asserts when the
    # guest's kernel reports none (no CONFIG_NUMA).
    if device == "cpu":
        serve_env["VLLM_CPU_OMP_THREADS_BIND"] = "nobind"
    if backend != "none":
        serve_env["LMCACHE_CONFIG_FILE"] = f"{GUEST_STATE_DIR}/{LMCACHE_CONFIG}"
    bench = {
        "KVBENCH_MODEL": model,
        "KVBENCH_DATASET": dataset,
        "KVBENCH_SESSIONS": int(sessions),
        "KVBENCH_WORKERS": int(workers),
        "KVBENCH_MAX_TOKENS": int(max_tokens),
    }
    if (dataset_revision or "").strip():
        bench["KVBENCH_DATASET_REVISION"] = dataset_revision.strip()
    bench_env = env_file(bench)
    files = {
        SERVE_CONFIG: json.dumps(serve, indent=1) + "\n",
        SERVE_ENV: env_file(serve_env) if serve_env else "",
        BENCH_ENV: bench_env,
    }
    if backend != "none":
        files[LMCACHE_CONFIG] = json.dumps(lmcache, indent=1) + "\n"

    share = share_dir(vm_name)
    for name, text in files.items():
        _emit(share / name, text)
    return {
        "presets": presets,
        "model": model,
        "backend": backend,
        "device": device,
    }
