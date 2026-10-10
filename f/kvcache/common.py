# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Shared library for the f/kvcache/* steps (host side of a KV cache run);
# imported as f.kvcache.common, not a runnable step. Touches only the host end
# of the rw virtiofs share (tag `kvcache`) the guest mounts at /var/lib/kvcache.
#
# The contract with the guest side (vendor/nixos-flake/modules/testSuites/
# kvcache.nix), kept verbatim on both ends:
#   * guest mount: /var/lib/kvcache (GUEST_STATE_DIR), share tag `kvcache`;
#   * <share>/vllm-serve.yaml  = `vllm serve --config` arguments (the unit
#     pins --host 127.0.0.1 --port 8000 on its command line, which wins);
#   * <share>/vllm-serve.env   = the server's EnvironmentFile;
#   * <share>/lmcache.yaml     = LMCache's LMCACHE_CONFIG_FILE;
#   * <share>/kvcache.env      = the replay's KVBENCH_* EnvironmentFile;
#   * <share>/<kver>/results/<preset>/summary.json = one replay's numbers
#     (kvcache@<preset> runs `kvcache-bench --output .../%v/results/%i`);
#   * <share>/<kver>/report.json = the run rollup f/kvcache/report writes;
#   * <share>/huggingface      = HF_HOME: models and the dataset, kept per VM;
#   * <share>/lmcache-disk     = the `disk` backend's offload, cleared per preset.
# vllm-serve.yaml and lmcache.yaml hold JSON, which is valid YAML, so writing
# them needs no YAML library.
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Re-exported so the steps import the transport and VM listing from one place,
# matching f/blktests/common; the transport itself lives in f.common.remote.
from f.common.remote import RemoteSystemd as RemoteSystemd
from f.common.remote import list_vms as list_vms

GUEST_STATE_DIR = "/var/lib/kvcache"

SERVE_CONFIG = "vllm-serve.yaml"
SERVE_ENV = "vllm-serve.env"
LMCACHE_CONFIG = "lmcache.yaml"
BENCH_ENV = "kvcache.env"

# The replay presets kvcache-bench ships (its PRESETS, name -> description); the
# list f/kvcache/discover reads from the guest's own kvcache-bench wins, this is
# the picker before the first discovery. A test keeps the two equal.
PRESETS = {
    "mixed-realistic": "every source, recorded gaps",
    "swebench-realistic": "code-repair sessions, recorded gaps",
    "swebench-burst": "code-repair sessions, no gaps",
    "gaia-realistic": "research sessions, recorded gaps",
    "gaia-burst": "research sessions, no gaps",
    "wildclaw-heavy": "long-context tool sessions, recorded gaps",
    "max-pressure": "every source, no gaps",
}
# The DEFAULT_* values are the setup the live run used (a CPU guest, the
# smallest model, one gaia-burst session, each turn capped at 8 tokens), though
# that run judged with max_failure_rate 1.0 and counted context failures; the
# shipped verdict defaults have not run live. The run form and render_config's
# schema repeat them; a test keeps them equal.
DEFAULT_PRESETS = ["gaia-burst"]

# The curated models: ungated chat models (a gated one needs a Hugging Face
# token), with the context window each config declares and the Hugging Face
# revision the server loads, so an A/B serves the same weights and tokenizer. A
# replay turn that outgrows the window fails as a `context` failure, so the
# window bounds which traces a model can serve whole.
MODELS = [
    {
        "id": "Qwen/Qwen2.5-0.5B-Instruct",
        "params": "0.5B",
        "context": 32768,
        "revision": "7ae557604adf67be50417f59c2c2f167def9a775",
    },
    {
        "id": "Qwen/Qwen2.5-7B-Instruct",
        "params": "7.6B",
        "context": 32768,
        "revision": "a09a35458c702b33eeacc393d103063234e8bc28",
    },
    {
        "id": "mistralai/Mistral-7B-Instruct-v0.3",
        "params": "7.2B",
        "context": 32768,
        "revision": "c170c708c41dac9275d15a8fff4eca08d52bab71",
    },
    {
        "id": "Qwen/Qwen3-8B",
        "params": "8.2B",
        "context": 40960,
        "revision": "b968826d9c46dd6066d109eabc6255188de91218",
    },
    {
        "id": "Qwen/Qwen3-32B",
        "params": "32.8B",
        "context": 40960,
        "revision": "9216db5781bf21249d130ec9da846c4624c16137",
    },
]
DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
DEFAULT_DATASET = "sammshen/lmcache-agentic-traces"
DEFAULT_DATASET_REVISION = "6e043b9e89865df3aec19fd5679286b683bfd70e"

# Where the KV cache LMCache offloads goes. `none` serves with vLLM's own prefix
# cache alone, the baseline the offload is measured against.
BACKENDS = {
    "cpu": "LMCache: offload to CPU memory",
    "disk": "LMCache: offload to CPU memory, then the share (the host's disk)",
    "none": "no offload: vLLM's own prefix cache alone",
}
DEFAULT_BACKEND = "none"
DEFAULT_SESSIONS = 1
DEFAULT_MAX_TOKENS = 8
DEFAULT_GPU_MEMORY_UTILIZATION = 0.5
DEFAULT_CPU_SIZE_GB = 20.0
DEFAULT_DISK_SIZE_GB = 50.0

LMCACHE_DISK_DIR = "lmcache-disk"

# `vllm serve` options the advanced override may not set: the unit pins the
# address on its command line, the model is the form's, which the replay names
# (as the served model name), and the LMCache wiring is the backend's.
_RESERVED_SERVE_ARGS = (
    "config",
    "host",
    "port",
    "model",
    "served-model-name",
    "kv-transfer-config",
)
# LMCache keys the advanced override may not set: the disk path is the one
# f/kvcache/start clears between presets.
_RESERVED_LMCACHE_KEYS = ("local_disk",)


def _workers() -> Path:
    return Path(os.environ["WORKERS_DIR"])


def share_dir(vm_name: str, workers: Path | None = None) -> Path:
    """Host path of the VM's `kvcache` virtiofs share, name-escape hardened.

    `$WORKERS_DIR/shared/kvcache/<vm_name>`, the dir f/qsu serves at
    /var/lib/kvcache; a crafted name (`../x`) can never write outside it.
    """
    root = (workers or _workers()) / "shared/kvcache"
    path = (root / vm_name).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"vm_name {vm_name!r} resolves outside {root}")
    return path


def lmcache_disk_dir(vm_name: str, workers: Path | None = None) -> Path:
    """`<share>/lmcache-disk`, where the `disk` backend offloads."""
    base = share_dir(vm_name, workers)
    path = (base / LMCACHE_DISK_DIR).resolve()
    if base not in path.parents:
        raise ValueError(f"{LMCACHE_DISK_DIR} resolves outside {base}")
    return path


def model_revision(model: str) -> str:
    """The pinned revision of a curated model; "" for any other model."""
    return next((m["revision"] for m in MODELS if m["id"] == model), "")


def _safe_segment(kind: str, value: str) -> str:
    """One path component, also usable as a systemd instance name."""
    v = (value or "").strip()
    if (
        not v
        or v in (".", "..")
        or "/" in v
        or "\\" in v
        or any(c.isspace() for c in v)
    ):
        raise ValueError(f"invalid {kind} {value!r}; expected one path component")
    return v


def results_dir(
    vm_name: str, kernel_version: str, preset: str, workers: Path | None = None
) -> Path:
    """`<share>/<kver>/results/<preset>`, the unit's `--output` for one replay."""
    base = share_dir(vm_name, workers)
    path = (
        base
        / _safe_segment("kernel_version", kernel_version)
        / "results"
        / _safe_segment("preset", preset)
    ).resolve()
    if base not in path.parents:
        raise ValueError(f"{kernel_version!r}/{preset!r} resolves outside {base}")
    return path


def report_path(
    vm_name: str, kernel_version: str = "", workers: Path | None = None
) -> Path:
    """`<share>/<kver>/report.json`; the share root when the kernel is unknown."""
    share = share_dir(vm_name, workers)
    if not (kernel_version or "").strip():
        return share / "report.json"
    return share / _safe_segment("kernel_version", kernel_version) / "report.json"


def presets_cache(vm_name: str, workers: Path | None = None) -> Path:
    """Per-VM cache of the guest's presets, which discover writes for the picker."""
    return share_dir(vm_name, workers) / "presets.json"


def _atomic_write(path: Path, data: str, mode: int = 0o644) -> None:
    """Write via a hidden temp file + rename so a concurrent reader on the shared
    dir (the guest's virtiofsd) never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def env_file(pairs: dict) -> str:
    """A systemd EnvironmentFile body: each value double-quoted, `\\` and `"`
    escaped, which systemd unquotes; a newline has no way through, so it is
    refused."""
    lines = []
    for key, value in pairs.items():
        text = str(value)
        if "\n" in text:
            raise ValueError(f"{key} holds a newline, which an EnvironmentFile cannot")
        quoted = text.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{quoted}"')
    return "\n".join(lines) + "\n"


def serve_config(
    model: str,
    max_model_len: int = 0,
    dtype: str = "auto",
    gpu_memory_utilization: float = DEFAULT_GPU_MEMORY_UTILIZATION,
    enable_prefix_caching: bool = True,
    backend: str = DEFAULT_BACKEND,
    extra: dict | None = None,
    revision: str = "",
) -> dict:
    """`vllm serve` arguments by their long option names; LMCache rides in as
    the KV connector unless the backend is `none`, and `revision` pins the
    model (vLLM's tokenizer follows it). `extra` (the advanced override, by
    long option name) replaces the form's settings when set: only the model,
    its revision and the LMCache wiring are kept, and the
    `_RESERVED_SERVE_ARGS` (and `revision`, when pinned) are refused."""
    reserved = _RESERVED_SERVE_ARGS + (("revision",) if revision else ())
    override: dict = {}
    for key, value in _extra("serve argument", extra).items():
        name = key.lstrip("-")
        if not name or name.replace("_", "-") in reserved:
            raise ValueError(
                f"serve argument {key!r} is not overridable: choose from vLLM's "
                f"options other than {', '.join(reserved)}"
            )
        override[name] = value
    cfg: dict = {"model": model}
    if revision:
        cfg["revision"] = revision
    if override:
        cfg.update(override)
    else:
        cfg["dtype"] = dtype
        cfg["gpu-memory-utilization"] = float(gpu_memory_utilization)
        cfg["enable-prefix-caching"] = bool(enable_prefix_caching)
        if max_model_len:
            cfg["max-model-len"] = int(max_model_len)
    if backend != "none":
        cfg["kv-transfer-config"] = json.dumps(
            {"kv_connector": "LMCacheConnectorV1", "kv_role": "kv_both"}
        )
    return cfg


def _extra(kind: str, extra: dict | None) -> dict:
    if not extra:
        return {}
    if not isinstance(extra, dict) or not all(isinstance(k, str) for k in extra):
        raise ValueError(f"the extra {kind}s must be an object keyed by name")
    return extra


def lmcache_config(
    backend: str,
    cpu_size_gb: float = DEFAULT_CPU_SIZE_GB,
    disk_size_gb: float = DEFAULT_DISK_SIZE_GB,
    extra: dict | None = None,
) -> dict:
    """LMCache's configuration file for an offload backend, by its own keys.
    `extra` (the advanced override) replaces the form's keys when set; the
    `disk` backend's path stays the share's, and is not overridable."""
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}: choose from {sorted(BACKENDS)}")
    override = _extra("LMCache key", extra)
    if backend == "none":
        if override:
            raise ValueError("LMCache keys need an offload backend, not `none`")
        return {}
    refused = [k for k in override if k in _RESERVED_LMCACHE_KEYS]
    if refused:
        raise ValueError(
            f"LMCache key(s) {refused} not overridable: the backend sets "
            f"{', '.join(_RESERVED_LMCACHE_KEYS)}"
        )
    if override:
        cfg: dict = dict(override)
    else:
        if cpu_size_gb <= 0:
            raise ValueError(f"cpu_size_gb must be above 0, got {cpu_size_gb}")
        cfg = {
            "chunk_size": 256,
            "local_cpu": True,
            "max_local_cpu_size": float(cpu_size_gb),
        }
        if backend == "disk":
            if disk_size_gb <= 0:
                raise ValueError(f"disk_size_gb must be above 0, got {disk_size_gb}")
            cfg["max_local_disk_size"] = float(disk_size_gb)
    if backend == "disk":
        cfg["local_disk"] = f"file://{GUEST_STATE_DIR}/{LMCACHE_DISK_DIR}/"
    return cfg


def counted_failure_rate(summary: dict) -> float:
    """The failure rate `item_status` judges by, `context` failures left out."""
    sessions = int(summary.get("sessions") or 0)
    if sessions <= 0:
        return 1.0
    failed = int(summary.get("sessions_failed") or 0)
    context = int((summary.get("failures_by_reason") or {}).get("context") or 0)
    return max(failed - context, 0) / sessions


def check_failure_rate(max_failure_rate: float) -> float:
    """`max_failure_rate` as a float in [0, 1], else a ValueError naming it."""
    rate = float(max_failure_rate)
    if not 0 <= rate <= 1:
        raise ValueError(f"max_failure_rate must be within [0, 1], got {rate}")
    return rate


def item_status(
    summary: dict | None,
    unit_ok: bool,
    crashed: bool,
    timed_out: bool,
    max_failure_rate: float,
) -> str:
    """One replay's verdict: the numbers exist, and few enough sessions failed.

    A crashed guest, a timed-out replay, or a unit that did not finish cleanly
    is `failed` even with a summary on the share. No summary, or one with no
    completed turn, is `notrun`: nothing was measured. Otherwise the failure
    rate is `(sessions_failed - failures_by_reason.context) / sessions`: a
    session that outgrew the model's context window does not count against
    the server. A rate above `max_failure_rate` fails it; the numbers' size
    never does.
    """
    if crashed or timed_out or not unit_ok:
        return "failed"
    if not summary or not summary.get("turns_completed"):
        return "notrun"
    if counted_failure_rate(summary) > float(max_failure_rate):
        return "failed"
    return "passed"


def run_status(per_preset: list) -> str:
    """The run verdict, the one rule report and judge share: `passed` only when
    every preset passed and there was at least one."""
    ok = bool(per_preset) and all(
        isinstance(p, dict) and p.get("status") == "passed" for p in per_preset
    )
    return "passed" if ok else "failed"


def _pick(entries: list[dict], filterText: str) -> list[dict]:
    needle = (filterText or "").lower()
    return [e for e in entries if needle in e["label"].lower()]


def list_presets(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_presets` entrypoint: the guest's replay presets,
    from discover's per-VM cache, else the catalog. Never raises."""
    presets = PRESETS
    vm = (vm_name or "").strip()
    if vm:
        try:
            cached = json.loads(presets_cache(vm).read_text())
            if isinstance(cached, dict) and cached:
                presets = cached
        except Exception:
            pass
    return _pick(
        [{"value": n, "label": f"{n}: {d}"} for n, d in presets.items()], filterText
    )


def list_models(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_models` entrypoint: the curated models with their size and
    context window."""
    return _pick(
        [
            {
                "value": m["id"],
                "label": f"{m['id']} ({m['params']}, {m['context'] // 1024}K context)",
            }
            for m in MODELS
        ],
        filterText,
    )


def main():
    """Library module imported by the f/kvcache/* steps; not a runnable step."""
    return "f/kvcache/common: share layout, presets, models, verdict rules"
