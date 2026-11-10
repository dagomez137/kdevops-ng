# SPDX-License-Identifier: copyleft-next-0.3.1
"""Render a KV cache run's config onto the `kvcache` virtiofs share.

Writes onto `$WORKERS_DIR/shared/kvcache/<vm>` (the host side of the guest's
`/var/lib/kvcache` mount) the files the guest's units read:

  - `vllm-serve.env`: `vllm-serve.service`'s EnvironmentFile. VLLM_MODEL,
    VLLM_SERVE_ARGS (max-model-len, dtype, gpu-memory-utilization, prefix
    caching, the LMCache KV-connector wiring) and the LMCACHE_* variables
    selecting the offload backend and buffer size.
  - `<preset>.env`: `kvcache-bench@<preset>.service`'s EnvironmentFile.
    Dataset, source filter, session cap, worker count, endpoint.

Both are written atomically and echoed to the job log. Because config
arrives over virtiofs rather than in the closure (the fstests contract),
changing a model, cache backend or preset needs no closure rebuild. The
host never contacts the guest.

Equivalent commands:

    cat > "$WORKERS_DIR/shared/kvcache/<vm>/vllm-serve.env"
    cat > "$WORKERS_DIR/shared/kvcache/<vm>/<preset>.env"
"""

from __future__ import annotations

from f.kvcache.common import (
    SERVE_ENV,
    _atomic_write,
    _safe_preset,
    env_file,
    workload_dir,
)
from f.kvcache.common import list_vms as _list_vms

# On a unified-memory host the engine's reservation comes out of the RAM the
# OS itself runs in; past this fraction the host thrashes and drops SSH.
LOCAL_GPU_MEM_MAX = 0.5


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(
    vm_name: str,
    preset: str,
    model: str = "facebook/opt-125m",
    max_model_len: int = 8192,
    dtype: str = "auto",
    gpu_memory_utilization: float = 0.85,
    enable_prefix_caching: bool = True,
    lmcache_enabled: bool = True,
    lmcache_cpu_buffer_gb: int = 40,
    lmcache_local_disk: str = "",
    extra_serve_args: str = "",
    dataset: str = "sammshen/lmcache-agentic-traces",
    source: str = "",
    max_sessions: int = 0,
    workers: int = 4,
    max_tokens: int = 256,
    api_port: int = 8000,
    target: str = "vm",
) -> dict:
    preset = _safe_preset(preset)
    share = workload_dir(vm_name, target)

    gpu_mem = float(gpu_memory_utilization)
    if target == "local" and gpu_mem > LOCAL_GPU_MEM_MAX:
        print(
            f"gpu_memory_utilization {gpu_mem} exceeds the local target's "
            f"unified-memory ceiling; clamped to {LOCAL_GPU_MEM_MAX}",
            flush=True,
        )
        gpu_mem = LOCAL_GPU_MEM_MAX

    serve_args = [
        f"--port {int(api_port)}",
        f"--max-model-len {int(max_model_len)}",
        f"--dtype {dtype}",
        f"--gpu-memory-utilization {gpu_mem}",
    ]
    if enable_prefix_caching:
        serve_args.append("--enable-prefix-caching")
    if lmcache_enabled:
        serve_args += [
            "--kv-transfer-config.kv_connector=LMCacheConnectorV1Dynamic",
            "--kv-transfer-config.kv_role=kv_both",
            "--kv-transfer-config.kv_connector_module_path="
            "lmcache.integration.vllm.lmcache_connector_v1",
        ]
    if extra_serve_args.strip():
        serve_args.append(extra_serve_args.strip())

    serve_env: dict[str, str] = {
        "VLLM_MODEL": model,
        "VLLM_SERVE_ARGS": " ".join(serve_args),
        # vllm-ready's health URL; the units expand ${API_PORT} in ExecStart.
        "API_PORT": str(int(api_port)),
    }
    if lmcache_enabled:
        serve_env["LMCACHE_MAX_LOCAL_CPU_SIZE"] = str(int(lmcache_cpu_buffer_gb))
        if lmcache_local_disk.strip():
            serve_env["LMCACHE_LOCAL_DISK"] = lmcache_local_disk.strip()

    bench_env = {
        "KVBENCH_ENDPOINT": f"http://127.0.0.1:{int(api_port)}",
        "KVBENCH_DATASET": dataset,
        "KVBENCH_SOURCE": source,
        "KVBENCH_SESSIONS": str(int(max_sessions)),
        "KVBENCH_WORKERS": str(int(workers)),
        "KVBENCH_MAX_TOKENS": str(int(max_tokens)),
        "KVBENCH_MODEL": model,
    }

    serve_path = share / SERVE_ENV
    bench_path = share / f"{preset}.env"
    _atomic_write(serve_path, env_file(serve_env))
    _atomic_write(bench_path, env_file(bench_env))
    for p, body in (
        (serve_path, env_file(serve_env)),
        (bench_path, env_file(bench_env)),
    ):
        print(f"wrote {p}:\n{body}", flush=True)
    return {
        "vm": vm_name,
        "preset": preset,
        "serve_env": str(serve_path),
        "bench_env": str(bench_path),
    }
