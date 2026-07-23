# SPDX-License-Identifier: copyleft-next-0.3.1
"""Collect a guest's QEMU sanitizer diagnostics from the host journal (runnable step).

A QEMU built under a sanitizer (`f/qemu/build`'s `sanitizer` knob) reports undefined
behaviour or memory errors in the emulator itself to its standard error, which the
guest's `qemu-system@<vm>.service` routes to the host journal under
`SyslogIdentifier=qemu-system@%i`. This step reads that journal, parses the sanitizer
lines through `f.qsu.diagnostics`, and returns a verdict. It is a property of any
guest run on a sanitized emulator, not a special test, so it stands alone: callable
against a VM already running, and composable as a tail step in `f/qsu/boot` and
`f/qsu/bringup`.

The read is scoped to the unit's current InvocationID by default, so it reports this
run's diagnostics rather than accumulating across restarts (a restart is a fresh QEMU
process, and each process re-reports its once); pass `scope=unit` for the whole unit
history. The worker reads the host journal directly because a worker is an ordinary
`systemd --user` service of the same user, not a container.

`wait_boot_secs` waits for the guest to finish booting before reading, because the
two kinds of diagnostic fire at different times: a device-realize one during QEMU
init (present the moment the unit is active), and a per-I/O one only once the guest
drives I/O to the device. The `boot` step returns as soon as the QEMU process is
active, which is before the guest OS has booted, so a read then catches the first
kind and misses the second. Polling the guest's `systemctl is-system-running` until
it settles (the shared "guest booted" signal) lets the guest's own boot I/O trip the
per-I/O site first. It never fails or hangs past the budget: an unreachable guest or
a timeout just falls through to the read. It stays 0 (no wait) for a standalone call
against a guest already up; the boot tail sets it.

The build's sanitizer selection decides how to read a quiet run (a sanitized build
that stayed clean, versus a stock build where a diagnostic was never possible). A
flow passes it from the build manifest; a standalone call leaves it empty and this
step derives it best-effort from the VM's recorded `qemu_binary` store name.

As a flow tail (`f/qsu/boot`, `f/qsu/bringup`) the step is passed the preceding
`boot` result and returns it verbatim with a `diagnostics` key added, so the flow
result stays the boot access banner enriched with the verdict rather than being
replaced by it. Called standalone (no `boot`), it returns the verdict alone.

Equivalent commands, against the host `systemd --user` manager:

    systemctl --user show qemu-system@<vm>.service --property=InvocationID --value
    journalctl --user _SYSTEMD_INVOCATION_ID=<id> --output=json
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from f.common.devshell import Systemd
from f.common.remote import RemoteSystemd
from f.qsu import diagnostics

# systemd system states that mean the guest OS finished booting (matches
# f/fstests/discover). `starting`/`initializing` mean it is still coming up.
_BOOTED = ("running", "degraded")


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.qsu.common.vm_options`."""
    # Imported lazily: the listing pulls in jinja2 (the template renderer), which the
    # run path never needs, so only the form-fill dynselect carries that dependency.
    from f.qsu.common import vm_options

    return vm_options(filterText)


def _messages(records_json: str) -> list[str]:
    """The MESSAGE of each journal record, in order; tolerant of the byte-array form."""
    out: list[str] = []
    for line in records_json.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("MESSAGE", "")
        if isinstance(msg, list):  # journalctl emits non-UTF-8 messages as byte arrays
            msg = bytes(msg).decode(errors="replace")
        out.append(msg)
    return out


def _wait_for_guest_boot(workers: Path, vm_name: str, budget: int) -> None:
    """Poll the guest's `systemctl is-system-running` until it settles or `budget` runs out.

    Best-effort: a guest with no vsock cid (not reachable this way) or one that never
    settles just returns, so the caller reads whatever the journal already holds rather
    than failing or hanging. The point is to let the guest's own boot I/O trip a per-I/O
    diagnostic before the read, not to gate on a healthy boot.
    """
    try:
        remote = RemoteSystemd(workers, vm_name)
    except ValueError as exc:
        print(f"{vm_name}: not waiting for guest boot ({exc})", flush=True)
        return
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        state = remote.is_system_running(quiet=True)
        if state in _BOOTED:
            print(f"{vm_name}: guest booted (is-system-running={state})", flush=True)
            return
        time.sleep(3)
    print(f"{vm_name}: guest not settled within {budget}s; reading anyway", flush=True)


def _detect_sanitizer(workers: Path, vm_name: str) -> str:
    """Best-effort sanitizer selection from the VM's recorded `qemu_binary` store name."""
    sidecar = workers / "shared/vm" / f"{vm_name}.vars.json"
    if not sidecar.is_file():
        return "none"
    try:
        binary = json.loads(sidecar.read_text()).get("qemu_binary") or ""
    except (json.JSONDecodeError, OSError):
        return "none"
    if not binary:
        return "none"
    # /nix/store/<hash>-qemu-<...>-<sanitizer>-<identity>/bin/qemu-system-<arch>
    return diagnostics.sanitizer_from_store_name(Path(binary).parents[1].name)


def main(
    vm_name: str,
    sanitizer: str = "",
    scope: str = "invocation",
    wait_boot_secs: int = 0,
    boot: dict | None = None,
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    systemd = Systemd(workers)
    unit = f"qemu-system@{vm_name}.service"

    if wait_boot_secs > 0:
        _wait_for_guest_boot(workers, vm_name, wait_boot_secs)

    if not sanitizer:
        sanitizer = _detect_sanitizer(workers, vm_name)
        print(f"{vm_name}: build sanitizer (detected) {sanitizer}", flush=True)
    else:
        print(f"{vm_name}: build sanitizer {sanitizer}", flush=True)

    match = f"_SYSTEMD_USER_UNIT={unit}"
    if scope == "invocation":
        inv = systemd.systemctl(
            "show",
            unit,
            "--property=InvocationID",
            "--value",
            capture=True,
            check=False,
        ).strip()
        if inv:
            match = f"_SYSTEMD_INVOCATION_ID={inv}"
        else:
            print(
                f"{vm_name}: no InvocationID; reading the whole unit journal",
                flush=True,
            )

    records = systemd.journalctl(
        match, "--output=json", "--no-pager", capture=True, check=False
    )
    findings = diagnostics.parse(_messages(records))
    verdict = diagnostics.verdict(findings, sanitizer)
    verdict["vm_name"] = vm_name

    for f in findings:
        site = f"{f['file']}:{f['line']}" if f["file"] else f["sanitizer"]
        print(
            f"  [{f['sanitizer']}/{f['category']}] {site}: {f['message']}", flush=True
        )
    print(
        f"{vm_name}: {verdict['status']} "
        f"({verdict['count']} diagnostic(s), {len(verdict['locations'])} site(s))",
        flush=True,
    )
    # As a flow tail the boot result is passed through as-is with the verdict added,
    # so the flow result stays the boot access banner rather than being replaced by
    # the verdict; standalone (no boot) returns the verdict alone.
    if boot:
        return {**boot, "diagnostics": verdict}
    return verdict
