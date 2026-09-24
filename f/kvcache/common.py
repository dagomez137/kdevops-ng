# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Shared library for the f/kvcache/* steps (KV cache workload orchestration).
# Not a runnable step; imported by the steps as f.kvcache.common.
#
# The contract with the guest (vendor/nixos-flake/modules/testSuites/kvcache.nix):
#   * guest mount: /var/lib/kvcache (GUEST_STATE_DIR), share tag `kvcache`;
#   * host side:   $WORKERS_DIR/shared/kvcache/<vm_name> (share_dir);
#   * the driver writes vllm-serve.env + <preset>.env onto the share, the
#     guest's units read them; results come back at <kver>/results/<preset>.
# Everything runnable in the guest is a systemd unit; these steps only start
# units over vsock-SSH and read their state.
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Re-exported so `from f.kvcache.common import RemoteSystemd, list_vms` mirrors
# the fstests/blktests import shape.
from f.common.remote import RemoteSystemd as RemoteSystemd
from f.common.remote import list_vms as list_vms

GUEST_STATE_DIR = "/var/lib/kvcache"
GUEST_TAG = "kvcache"

# Keep in sync with PRESETS in vendor/nixos-flake/pkgs/kvcache-bench/. The
# picker cache written by discover carries whatever the guest's packaged
# replayer actually advertises; this list is only the pre-discover fallback.
PRESETS = [
    "swebench-realistic",
    "swebench-burst",
    "gaia-realistic",
    "gaia-burst",
    "wildclaw-heavy",
    "max-pressure",
]

SERVE_ENV = "vllm-serve.env"


def _workers() -> Path:
    return Path(os.environ["WORKERS_DIR"])


def share_dir(vm_name: str, workers: Path | None = None) -> Path:
    """Host path of the VM's `kvcache` virtiofs share, name-escape hardened.

    `$WORKERS_DIR/shared/kvcache/<vm_name>`. Lives under `shared/` so every
    worker sees the same bytes the guest's virtiofsd serves. `vm_name` is
    resolved and checked to sit directly under the share root, so a crafted
    name (`../x`) can never write outside it.
    """
    root = (workers or _workers()) / "shared/kvcache"
    path = (root / vm_name).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"vm_name {vm_name!r} resolves outside {root}")
    return path


def _safe_kver(kernel_version: str) -> str:
    """Validate a `uname -r` string is a single path component (no `/`, no `..`)."""
    kv = (kernel_version or "").strip()
    if not kv or "/" in kv or kv in (".", ".."):
        raise ValueError(
            f"kernel_version {kernel_version!r} is not a safe path segment"
        )
    return kv


def _safe_preset(preset: str) -> str:
    """A preset is a systemd instance name and a path segment; keep it plain."""
    p = (preset or "").strip()
    if not p or any(c in p for c in "/\\ \t") or p in (".", ".."):
        raise ValueError(f"preset {preset!r} is not a safe instance name")
    return p


def results_dir(
    vm_name: str, kernel_version: str, preset: str, workers: Path | None = None
) -> Path:
    """`<share_dir>/<kver>/results/<preset>`: the host view of one run's output.

    Mirrors the unit's `--output ${stateDir}/%v/results/%i`, so results from
    the same closure booted with different kernels never collide.
    """
    kv = _safe_kver(kernel_version)
    p = _safe_preset(preset)
    base = (share_dir(vm_name, workers) / kv / "results").resolve()
    out = (base / p).resolve()
    if base not in out.parents:
        raise ValueError(f"preset {preset!r} resolves outside {base}")
    return out


def presets_cache(vm_name: str, workers: Path | None = None) -> Path:
    """Per-VM picker cache the run form's preset dropdown reads (discover writes)."""
    return share_dir(vm_name, workers) / ".presets.json"


def _atomic_write(path: Path, data: str, mode: int = 0o644) -> None:
    """Write via a hidden temp file + rename so a concurrent reader on the shared
    dir (the guest's virtiofsd) never sees a half-written env/config file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def env_file(pairs: dict[str, str]) -> str:
    """Render a systemd EnvironmentFile body. Values are single-quoted with
    embedded quotes escaped, so word-splitting happens only where a unit reads
    an unquoted `$VAR` on purpose (VLLM_SERVE_ARGS)."""
    lines = []
    for k, v in pairs.items():
        vv = str(v).replace("'", "'\\''")
        lines.append(f"{k}='{vv}'")
    return "\n".join(lines) + "\n"


def main():
    """Library module imported by the f/kvcache/* steps; not a runnable step."""
    return "f/kvcache/common: share layout, presets, env-file helpers"
