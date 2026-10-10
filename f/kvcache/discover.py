# SPDX-License-Identifier: copyleft-next-0.3.1
"""Discover a booted guest's kvcache readiness over vsock-SSH (read-only).

Checks the guest is up and kvcache-ready (the `kvcache@.service` template and
`vllm-serve.service` present, and /var/lib/kvcache the mounted `kvcache`
share rather than a directory on the guest's own disk), asks the guest's own
kvcache-bench for its replay presets, and reports whether an NVIDIA GPU
reached it: `cuda` only when `nvidia-smi` exits 0, since it prints its error
to stdout when the driver is not loaded. The device the server runs on decides
whether LMCache can offload at all, since it has no connector for a CPU
engine. It also reports whether the guest's kernel exposes a NUMA node and the
process's allowed memory nodes (`Mems_allowed_list`, which only a
CONFIG_CPUSETS kernel prints), both of which vLLM's CPU backend reads;
f/kvcache/render_config refuses a CPU engine without them. A guest offering
zero presets fails here, as a run would replay nothing. The presets are
written to the per-VM picker cache on the share, the source the run form's
preset dropdown reads. Mutates nothing on the guest.

Equivalent commands, against the guest over vsock-SSH:

    systemctl --host <vm> is-system-running
    systemctl --host <vm> list-unit-files kvcache@.service --no-legend
    systemctl --host <vm> list-unit-files vllm-serve.service --no-legend
    ssh <vm> mountpoint --quiet /var/lib/kvcache
    ssh <vm> kvcache-bench --list-presets
    ssh <vm> nvidia-smi --query-gpu=name --format=csv,noheader
    ssh <vm> test -d /sys/devices/system/node/node0
    ssh <vm> grep Mems_allowed_list /proc/self/status
    ssh <vm> cat /proc/sys/kernel/osrelease
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from f.kvcache.common import (
    GUEST_STATE_DIR,
    RemoteSystemd,
    _atomic_write,
    presets_cache,
)
from f.kvcache.common import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)

    system_state = remote.is_system_running()
    if system_state not in ("running", "degraded"):
        raise RuntimeError(
            f"{vm_name}: guest not booted (is-system-running={system_state!r}); "
            f"boot it with f/qsu/boot before running kvcache"
        )
    missing = [
        u
        for u in ("kvcache@.service", "vllm-serve.service")
        if not remote.unit_exists(u)
    ]
    if missing:
        raise RuntimeError(
            f"{vm_name}: not kvcache-ready ({', '.join(missing)} missing); bring the "
            f"guest up with the kvcache test suite in the closure"
        )
    rc = remote.ssh(
        "mountpoint", "--quiet", GUEST_STATE_DIR, capture=False, check=False
    )
    if rc != 0:
        raise RuntimeError(
            f"{vm_name}: {GUEST_STATE_DIR} is not the mounted kvcache share "
            f"(mountpoint exited {rc}), so the host can neither configure the "
            "run nor read its results; bring the guest up with the kvcache test "
            "suite so f/qsu/boot mounts the share"
        )

    presets = json.loads(remote.ssh("kvcache-bench", "--list-presets") or "{}")
    if not presets:
        raise RuntimeError(
            f"{vm_name}: kvcache-bench lists no preset; a run would replay nothing"
        )
    cache = presets_cache(vm_name)
    _atomic_write(cache, json.dumps(presets) + "\n")
    print(f"wrote {cache} ({len(presets)} presets for the run form)", flush=True)

    try:
        gpu = (
            remote.ssh("nvidia-smi", "--query-gpu=name", "--format=csv,noheader") or ""
        ).strip()
    except subprocess.CalledProcessError:
        gpu = ""
    device = "cuda" if gpu else "cpu"
    numa = (
        remote.ssh(
            "test", "-d", "/sys/devices/system/node/node0", capture=False, check=False
        )
        == 0
        and remote.ssh(
            "grep", "Mems_allowed_list", "/proc/self/status", capture=False, check=False
        )
        == 0
    )
    kernel_version = (remote.ssh("cat", "/proc/sys/kernel/osrelease") or "").strip()
    if not kernel_version:
        raise RuntimeError(f"{vm_name}: could not read the guest's kernel release")
    print(
        f"{vm_name}: booted={system_state} kvcache_ready=True device={device} "
        f"gpu={gpu or 'none'} numa={numa} presets={len(presets)} "
        f"kernel={kernel_version}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "kernel_version": kernel_version,
        "device": device,
        "gpu": gpu or None,
        "numa": numa,
        "presets": sorted(presets),
    }
