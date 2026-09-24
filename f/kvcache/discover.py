# SPDX-License-Identifier: copyleft-next-0.3.1
"""Discover a booted guest's kvcache readiness over vsock-SSH (read-only).

Checks the guest is up and kvcache-ready: the `kvcache-bench@.service`
template and `vllm-serve.service` are present (the closure carried the
kvcache suite), the packaged replayer is executable at the path the unit's
own `ExecStart` names, the `/var/lib/kvcache` share is mounted and writable
(the units' EnvironmentFile contract), and a GPU node is visible when the
`gpu` profile is expected to have loaded a driver. Enumerates the running
kernel release and the replayer's presets, and writes the presets to the
per-VM picker cache on the share -- the source the run form's dropdown
reads. Mutates nothing on the guest.

Equivalent commands, against the guest over vsock-SSH:

    systemctl --host <vm> is-system-running
    systemctl --host <vm> list-unit-files \\
        kvcache-bench@.service vllm-serve.service
    systemctl --host <vm> show kvcache-bench@probe.service \\
        --property=ExecStart
    ssh <vm> test -x <ExecStart path>
    ssh <vm> sh -c 'test -d /var/lib/kvcache \\
        && touch /var/lib/kvcache/.probe'
    ssh <vm> ls /dev/nvidia0 /dev/kfd
    ssh <vm> cat /proc/sys/kernel/osrelease
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from f.kvcache.common import (
    GUEST_STATE_DIR,
    PRESETS,
    RemoteSystemd,
    _atomic_write,
    presets_cache,
)
from f.kvcache.common import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


_EXEC_PATH_RE = re.compile(r"path=([^\s;]+)")


def main(vm_name: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)

    system_state = remote.is_system_running()
    if system_state not in ("running", "degraded"):
        raise RuntimeError(
            f"{vm_name}: guest not booted (is-system-running={system_state!r}); "
            f"boot it with f/qsu/boot before running the kvcache workload"
        )

    bench_present = remote.unit_exists("kvcache-bench@.service")
    serve_present = remote.unit_exists("vllm-serve.service")
    if not (bench_present and serve_present):
        raise RuntimeError(
            f"{vm_name}: not kvcache-ready (kvcache-bench@.service "
            f"{'present' if bench_present else 'missing'}, vllm-serve.service "
            f"{'present' if serve_present else 'missing'}); bring the guest up "
            f"with the kvcache test suite (and the gpu profile) in the closure"
        )

    props = remote.show("kvcache-bench@probe.service", "ExecStart")
    m = _EXEC_PATH_RE.search(props.get("ExecStart", ""))
    bench_path = m.group(1) if m else ""
    bench_exec = bool(bench_path) and (
        remote.ssh("test", "-x", bench_path, capture=False, check=False) == 0
    )
    if not bench_exec:
        raise RuntimeError(
            f"{vm_name}: packaged replayer {bench_path or 'unresolved'} not "
            f"executable; the closure's kvcache-bench package is broken"
        )

    # The units' EnvironmentFile contract: the share must be mounted here and
    # writable from the host side before render_config can do anything.
    share_ok = (
        remote.ssh(
            "sh",
            "-c",
            f"test -d {GUEST_STATE_DIR} && touch {GUEST_STATE_DIR}/.probe "
            f"&& rm -f {GUEST_STATE_DIR}/.probe",
            capture=False,
            check=False,
        )
        == 0
    )
    if not share_ok:
        raise RuntimeError(
            f"{vm_name}: {GUEST_STATE_DIR} missing or not writable; the "
            f"`kvcache` share is not mounted (check f/qsu shares + boot)"
        )

    # Advisory, not gating: a GPU node. The engine fails loudly without one,
    # but surfacing it here saves a model-load-long wait to find out.
    gpu_node = (
        remote.ssh(
            "sh", "-c", "ls /dev/nvidia0 /dev/kfd 2>/dev/null | head -1", check=False
        )
        or ""
    ).strip()

    kernel_version = (remote.ssh("cat", "/proc/sys/kernel/osrelease") or "").strip()

    _atomic_write(presets_cache(vm_name), json.dumps({"presets": PRESETS}) + "\n")
    print(
        f"{vm_name}: kvcache-ready (kernel {kernel_version}, replayer {bench_path}, "
        f"gpu_node={gpu_node or 'NONE'}, presets={len(PRESETS)})",
        flush=True,
    )
    return {
        "vm": vm_name,
        "kvcache_ready": True,
        "kernel_version": kernel_version,
        "bench_path": bench_path,
        "gpu_node": gpu_node or None,
        "presets": PRESETS,
    }
