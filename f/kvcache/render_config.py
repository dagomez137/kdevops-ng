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
    share_dir,
)
from f.kvcache.common import list_vms as _list_vms


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
) -> dict:
    preset = _safe_preset(preset)
    share = share_dir(vm_name)

    serve_args = [
        f"--max-model-len {int(max_model_len)}",
        f"--dtype {dtype}",
        f"--gpu-memory-utilization {float(gpu_memory_utilization)}",
    ]
    if enable_prefix_caching:
        serve_args.append("--enable-prefix-caching")
    if lmcache_enabled:
        serve_args.append(
            "--kv-transfer-config "
            '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_both"}'
        )
    if extra_serve_args.strip():
        serve_args.append(extra_serve_args.strip())

    serve_env: dict[str, str] = {
        "VLLM_MODEL": model,
        "VLLM_SERVE_ARGS": " ".join(serve_args),
    }
    if lmcache_enabled:
        serve_env["LMCACHE_MAX_LOCAL_CPU_SIZE"] = str(int(lmcache_cpu_buffer_gb))
        if lmcache_local_disk.strip():
            serve_env["LMCACHE_LOCAL_DISK"] = lmcache_local_disk.strip()

    bench_env = {
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
