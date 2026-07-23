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

The build's sanitizer selection decides how to read a quiet run (a sanitized build
that stayed clean, versus a stock build where a diagnostic was never possible). A
flow passes it from the build manifest; a standalone call leaves it empty and this
step derives it best-effort from the VM's recorded `qemu_binary` store name.

Equivalent commands, against the host `systemd --user` manager:

    systemctl --user show qemu-system@<vm>.service --property=InvocationID --value
    journalctl --user _SYSTEMD_INVOCATION_ID=<id> --output=json
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from f.common.devshell import Systemd
from f.qsu import diagnostics


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
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    systemd = Systemd(workers)
    unit = f"qemu-system@{vm_name}.service"

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
    result = diagnostics.verdict(findings, sanitizer)
    result["vm_name"] = vm_name

    for f in findings:
        site = f"{f['file']}:{f['line']}" if f["file"] else f["sanitizer"]
        print(
            f"  [{f['sanitizer']}/{f['category']}] {site}: {f['message']}", flush=True
        )
    print(
        f"{vm_name}: {result['status']} "
        f"({result['count']} diagnostic(s), {len(result['locations'])} site(s))",
        flush=True,
    )
    return result
