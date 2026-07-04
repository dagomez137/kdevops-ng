# SPDX-License-Identifier: copyleft-next-0.3.1
"""Render a kselftest run's host-side config onto the `selftests` share.

Writes `<share>/kselftest.env`, the systemd `EnvironmentFile` both guest
templates read: `KSELFTEST_ARGS=<extra run_kselftest.sh flags>`. The one knob
is the per-test timeout: `override_timeout` > 0 emits `-o <seconds>`
(`run_kselftest.sh --override-timeout`), replacing every collection's own
upstream `settings` timeout (default 45 s per test); 0 emits no flag and keeps
the upstream timeouts. Written atomically and echoed to the job log. The host
never contacts the guest.

Equivalent command:

    cat > "$WORKERS_DIR/shared/selftests/<vm>/kselftest.env"
"""

from __future__ import annotations

from f.common.remote import list_vms as _list_vms
from f.selftests.common import _atomic_write, share_dir


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def main(vm_name: str, override_timeout: int = 0) -> dict:
    args = f"-o {int(override_timeout)}" if override_timeout else ""
    env_path = share_dir(vm_name) / "kselftest.env"
    text = f"KSELFTEST_ARGS={args}\n"
    _atomic_write(env_path, text)
    print(f"+ wrote {env_path}", flush=True)
    print(text, flush=True)
    return {
        "vm": vm_name,
        "env_path": str(env_path),
        "kselftest_args": args,
    }
