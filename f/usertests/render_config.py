# SPDX-License-Identifier: copyleft-next-0.3.1
"""Render the per-harness env files onto the `usertests` share.

Writes one `<share>/<kver>/env/<dir>/<binary>.env` per discovered harness, the
per-instance `EnvironmentFile` the guest template reads
(`EnvironmentFile=-/var/lib/usertests/%v/env/%I.env`; `%I` unescapes back to
the item path). Each file carries one `ARGS=` line: `radix-tree/main` composes
its flags from the run form's knobs (`-s <seed>` when seed > 0, `-l` when
long_run; it logs `random seed %u` either way, so an unseeded run's seed is
still archived), `memblock/main` gets its fixed `--verbose` (the per-test
`: passed` lines collect counts), and every other harness an empty `ARGS=`.
Written atomically and echoed to the job log. The host never contacts the
guest.

Equivalent command, per harness:

    cat > "$WORKERS_DIR/shared/usertests/<vm>/<kver>/env/<dir>/<binary>.env"
"""

from __future__ import annotations

from f.common.remote import list_vms as _list_vms
from f.usertests.common import _atomic_write, item_args, share_dir


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(
    vm_name: str,
    kernel_version: str,
    harnesses: list[str] | None = None,
    seed: int = 0,
    long_run: bool = False,
) -> dict:
    env_root = share_dir(vm_name) / kernel_version / "env"
    written: dict[str, str] = {}
    for item in list(harnesses or []):
        args = item_args(item, seed=seed, long_run=long_run)
        path = env_root / f"{item}.env"
        text = f"ARGS={args}\n"
        _atomic_write(path, text)
        print(f"+ wrote {path}", flush=True)
        print(text, flush=True)
        written[item] = args
    print(f"{vm_name}: {len(written)} env file(s) under {env_root}", flush=True)
    return {
        "vm": vm_name,
        "env_root": str(env_root),
        "args": written,
        "seed": int(seed),
        "long_run": bool(long_run),
    }
