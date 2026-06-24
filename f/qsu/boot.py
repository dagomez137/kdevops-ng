# SPDX-License-Identifier: copyleft-next-0.3.1
"""Boot one QEMU/systemd VM: daemon-reload, then `systemctl restart qemu-system@<vm>`.

A single `restart` of the qemu-system unit is the whole lifecycle — the qsu units are
designed for first-class `systemctl start/stop/restart`, so we never touch the virtiofsd
.socket/.service directly. The per-VM drop-in pins virtiofsd with
`Requires=virtiofsd@%i-<tag>.service` (not `BindsTo=`) and the virtiofsd side carries a
`Before=qemu-system@<vm>.service` drop-in, so `restart` stops the old guest gracefully
(virtiofsd outlives the ExecStop powerdown) and, on start, the QEMU process
socket-activates a fresh virtiofsd that reads the re-rendered per-share env. Restarting
the virtiofsd units by hand instead propagates a stop into qemu mid-graceful-shutdown
and wedges the guest on its now-dead virtiofs mounts until `TimeoutStopSec` (2 min)
SIGKILLs it — see qsu docs/usage.md "Updating a VM's unit definition".

`restart` (not `start`) so a re-render of an already-running VM takes effect — a
reconfigure in place; on a stopped VM it just starts. Then poll `is-active` until the
unit is `active` (Type=simple) or `failed`. A unit that does not reach `active` (the
qemu process exited — a bad `-device`, an unbootable kernel, a missing share) raises
with the guest's own journal tail, so the flow fails at boot instead of reporting
success on a VM that never came up. `ssh_ready` is a best-effort probe of the forwarded
port; it only succeeds where the prober shares the host's network namespace, so from a
worker verify the guest over vsock instead.

Tradeoff (qsu docs/design-decisions, "virtiofsd dependency"): `Requires=` loses
automatic crash-kill propagation if virtiofsd dies unexpectedly, accepted so that
`systemctl restart` stays a first-class single-unit operation.

Equivalent commands, against the host `systemd --user` manager:

    systemctl --user daemon-reload
    systemctl --user restart qemu-system@<vm>.service
"""

from __future__ import annotations

import os
import socket as _socket
import tempfile
import time
from pathlib import Path

from f.common.devshell import Systemd, system_dir
from f.qsu.common import state_dir


def _atomic_write(path: Path, data: str, mode: int = 0o644) -> None:
    """Write via a hidden temp file + rename so a concurrent reader of the shared
    ssh dir never globs a half-written `.conf` (a partial stanza aborts ssh's parse)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _ssh_banner(port: int, timeout: float = 3.0) -> bool:
    try:
        with _socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            return sock.recv(16).startswith(b"SSH-2.0")
    except OSError:
        return False


def _write_ssh_alias(vm_name: str, vsock_cid: int | None) -> str | None:
    """Write this VM's `Host <vm>` block into $SYSTEM_DIR/ssh/config.d so `ssh <vm>` works.

    Needs the kdevops-managed key (f/workbench/ssh_key) and a vsock cid; the block
    routes `<vm>` over vsock with that key, picked up by the operator's one-time
    `Include $SYSTEM_DIR/ssh/config` in ~/.ssh/config.
    """
    priv = system_dir() / "ssh/id_ed25519"
    if not vsock_cid or not priv.is_file():
        return None
    conf = system_dir() / "ssh/config.d" / f"{vm_name}.conf"
    _atomic_write(conf, "\n".join([
        f"Host {vm_name}",
        f"    HostName vsock/{vsock_cid}",
        "    ProxyCommand /usr/lib/systemd/systemd-ssh-proxy %h %p",
        "    ProxyUseFdpass yes",
        f"    IdentityFile {priv}",
        "    IdentitiesOnly yes",
        "    User root",
        "    StrictHostKeyChecking accept-new",
        "    UserKnownHostsFile /dev/null",
        "",
    ]))
    print(f"wrote {conf}", flush=True)
    return str(conf)


def main(
    vm_name: str,
    ssh_port: int,
    vsock_cid: int | None = None,
    wait_timeout: int = 300,
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    systemd = Systemd(workers)
    # daemon-reload picks up the re-rendered unit + drop-in; then one restart of the
    # qemu-system unit is the whole lifecycle. systemd's own dependencies stop the old
    # guest gracefully and socket-activate a fresh virtiofsd on start — we never restart
    # the virtiofsd .socket/.service ourselves (doing so propagates a stop into qemu and
    # hangs it to TimeoutStopSec). restart, not start: `start` no-ops on a running unit
    # so a re-render would not take effect; restart applies it in place (a reconfigure),
    # and for a stopped VM it just starts.
    systemd.systemctl("daemon-reload")
    unit = f"qemu-system@{vm_name}.service"
    systemd.systemctl("restart", unit)

    deadline = time.monotonic() + min(int(wait_timeout), 60)
    state = ""
    while time.monotonic() < deadline:
        state = (systemd.systemctl("is-active", unit, capture=True, check=False) or "").strip()
        if state in ("active", "failed"):
            break
        time.sleep(2)
    active = state == "active"
    if not active:
        # A restart that returns 0 only means systemd started the unit; a Type=simple
        # qemu that exits right after (bad -device, unbootable kernel, missing share)
        # lands in `failed` moments later. Fail the job — otherwise the flow reports
        # success on a VM that never booted. systemctl reaches the host manager over
        # D-Bus so the unit's exit status is available, but the qemu stderr that holds
        # the actual reason is in the host journal, which the worker cannot read; point
        # the operator at the journalctl command that shows it on the host.
        props = (systemd.systemctl(
            "show", unit, "--property=Result,ExecMainStatus,ExecMainCode",
            capture=True, check=False) or "").strip().replace("\n", " ")
        raise RuntimeError(
            f"{unit} did not come up (state={state or 'unknown'}{'; ' + props if props else ''}); "
            f"the guest failed to boot. Reason: journalctl --user-unit={unit}"
        )
    ssh_ready = _ssh_banner(int(ssh_port))

    ssh_config = _write_ssh_alias(vm_name, vsock_cid)
    if ssh_config:
        ssh_command = f"ssh {vm_name}"
    elif vsock_cid:
        ssh_command = f"ssh root@vsock/{vsock_cid}"
    else:
        ssh_command = f"ssh -p {ssh_port} root@127.0.0.1"

    print(f"qemu-system@{vm_name} is {state or 'unknown'}; access: {ssh_command}", flush=True)
    return {
        "vm_name": vm_name,
        "ssh_port": int(ssh_port),
        "vsock_cid": int(vsock_cid) if vsock_cid else None,
        "ssh_command": ssh_command,
        "ssh_config": ssh_config,
        "state_dir": str(state_dir(vm_name)),
        "active": active,
        "ssh_ready": ssh_ready,
    }
