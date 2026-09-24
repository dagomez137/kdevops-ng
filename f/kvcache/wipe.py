# SPDX-License-Identifier: copyleft-next-0.3.1
"""Remove one preset's prior results for one kernel from the share (host side).

Deletes `<share>/<kver>/results/<preset>` so a fresh run starts from a clean
directory. Scoped to exactly one (vm, kernel, preset) triple and path-escape
hardened in common.results_dir; never touches the env files or other
kernels' results.

Equivalent commands:

    rm -rf "$WORKERS_DIR/shared/kvcache/<vm>/<kver>/results/<preset>"
"""

from __future__ import annotations

import shutil

from f.kvcache.common import list_vms as _list_vms
from f.kvcache.common import results_dir


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, preset: str, kernel_version: str) -> dict:
    out = results_dir(vm_name, kernel_version, preset)
    existed = out.is_dir()
    if existed:
        shutil.rmtree(out)
    print(f"{'removed' if existed else 'no prior results at'} {out}", flush=True)
    return {
        "vm": vm_name,
        "preset": preset,
        "removed": existed,
        "results_dir": str(out),
    }
