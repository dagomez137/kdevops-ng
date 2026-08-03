# SPDX-License-Identifier: copyleft-next-0.3.1
"""Discover a booted guest's blktests readiness over vsock-SSH (read-only).

Checks the guest is up and blktests-ready (`blktests@.service` template present
and its packaged `./check` runner executable, probed at the path the unit's own
`ExecStart` names), then enumerates the NVMe data disks (`/dev/nvme*n1`), the
installed test groups (the `tests/*/rc` entries under the package tree, derived
from the unit's `WorkingDirectory`), and the running kernel release. A guest
exposing zero groups fails here: a run would silently test nothing. Devices may
be empty (the default `nodev` groups create their own devices); they matter
only for a `TEST_DEVS` run. The groups, tests and devices are written together
to the per-VM picker cache on the share, the source the run form's dropdowns
read. Mutates nothing on the guest.

Equivalent commands, against the guest over vsock-SSH:

    systemctl --host <vm> is-system-running
    systemctl --host <vm> list-unit-files blktests@.service
    systemctl --host <vm> show blktests@probe.service \
        --property=ExecStart --property=WorkingDirectory
    ssh <vm> test -x <ExecStart path>
    ssh <vm> sh -c 'ls --directory <package>/tests/*/rc'
    ssh <vm> sh -c 'ls <package>/tests/*/[0-9][0-9][0-9]'
    ssh <vm> lsblk --nodeps --noheadings --output NAME,SIZE,TYPE,LOG-SEC
    ssh <vm> cat /proc/sys/kernel/osrelease
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from f.blktests.common import RemoteSystemd, _atomic_write, groups_cache
from f.blktests.common import list_vms as _list_vms


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


_EXEC_PATH_RE = re.compile(r"path=([^\s;]+)")


def _unit_paths(remote: RemoteSystemd) -> tuple[str, str]:
    """The packaged `./check` path and the package directory, from the unit itself.

    `ExecStart` names the runner (`{ path=<pkg>/blktests/check ; argv[]=... }`)
    and `WorkingDirectory` the package tree `check` runs from; the working
    directory falls back to the runner's parent, so either property alone
    suffices. Probing the unit's own paths keeps this accurate for any package
    location, with nothing hardcoded. `show` refuses a bare template name, so
    probe a throwaway instance; systemd renders the template's properties for
    any instance name.
    """
    props = remote.show("blktests@probe.service", "ExecStart", "WorkingDirectory")
    m = _EXEC_PATH_RE.search(props.get("ExecStart", ""))
    check_path = m.group(1) if m else ""
    package_dir = props.get("WorkingDirectory", "") or (
        str(Path(check_path).parent) if check_path else ""
    )
    return check_path, package_dir


def _groups(remote: RemoteSystemd, package_dir: str) -> list[str]:
    """The installed test groups: the `tests/*/rc` entries under the package tree.

    The glob must expand in the guest (the transport quotes argv tokens), so this
    is one `sh -c`; `check=False` because zero matches is a valid answer (the
    caller refuses it).
    """
    script = f"ls --directory {package_dir}/tests/*/rc"
    out = remote.ssh("sh", "-c", script, check=False) or ""
    prefix = f"{package_dir}/tests/"
    return sorted(
        {
            p.split("/")[-2]
            for p in out.split()
            if p.startswith(prefix) and p.endswith("/rc")
        }
    )


def _tests(remote: RemoteSystemd, package_dir: str) -> list[str]:
    """The installed individual tests (`group/nnn`), for the form's pickers."""
    script = f"ls {package_dir}/tests/*/[0-9][0-9][0-9]"
    out = remote.ssh("sh", "-c", script, check=False) or ""
    prefix = f"{package_dir}/tests/"
    return sorted(
        p.removeprefix(prefix)
        for p in out.split()
        if p.startswith(prefix) and p.split("/")[-1].isdigit()
    )


def _devices(remote: RemoteSystemd) -> list[dict]:
    """The guest's NVMe data disks (`/dev/nvme*n1`) as `{name, size, log_sec}`, file order.

    Lists whole disks (no partitions); keeps the `disk`-type `nvme*n1` namespaces,
    the ones a `TEST_DEVS` run can target. `log_sec` is the device's logical
    sector size in bytes (lsblk `LOG-SEC`), defaulting to 512 when lsblk omits a
    parseable value.
    """
    out = (
        remote.ssh(
            "lsblk", "--nodeps", "--noheadings", "--output", "NAME,SIZE,TYPE,LOG-SEC"
        )
        or ""
    )
    devices: list[dict] = []
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        name, size, kind = fields[0], fields[1], fields[2]
        log_sec = int(fields[3]) if len(fields) >= 4 and fields[3].isdigit() else 512
        if kind == "disk" and name.startswith("nvme") and name.endswith("n1"):
            devices.append({"name": f"/dev/{name}", "size": size, "log_sec": log_sec})
    return devices


def _kernel_release(remote: RemoteSystemd) -> str:
    """The guest's running kernel release (`uname -r`), from `/proc/sys/kernel/osrelease`.

    This is the same value the systemd `%v` specifier resolves to in the
    `blktests@.service` unit, so the host keys results under the identical
    `<kver>` the guest writes to.
    """
    out = (remote.ssh("cat", "/proc/sys/kernel/osrelease") or "").strip()
    if not out:
        raise RuntimeError(
            "could not read /proc/sys/kernel/osrelease (uname -r) from guest"
        )
    return out


def main(vm_name: str) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    remote = RemoteSystemd(workers, vm_name)

    system_state = remote.is_system_running()
    booted = system_state in ("running", "degraded")
    if not booted:
        raise RuntimeError(
            f"{vm_name}: guest not booted (is-system-running={system_state!r}); "
            f"boot it with f/qsu/boot before running blktests"
        )

    unit_present = remote.unit_exists("blktests@.service")
    check_path, package_dir = _unit_paths(remote) if unit_present else ("", "")
    check_present = bool(check_path) and (
        remote.ssh("test", "-x", check_path, capture=False, check=False) == 0
    )
    blktests_ready = unit_present and check_present
    if not blktests_ready:
        raise RuntimeError(
            f"{vm_name}: not blktests-ready (blktests@.service "
            f"{'present' if unit_present else 'missing'}, check runner "
            f"{check_path or 'unresolved'} "
            f"{'present' if check_present else 'missing'}); bring the guest up "
            f"with the blktests test suite in the closure"
        )

    groups = _groups(remote, package_dir)
    if not groups:
        raise RuntimeError(
            f"{vm_name}: no test groups under {package_dir}/tests: the packaged "
            f"blktests ships nothing to run; a run would silently test nothing"
        )
    tests = _tests(remote, package_dir)
    devices = _devices(remote)
    cache = groups_cache(vm_name)
    _atomic_write(
        cache,
        json.dumps({"groups": groups, "tests": tests, "devices": devices}) + "\n",
    )
    print(
        f"+ wrote {cache} ({len(groups)} groups, {len(tests)} tests, "
        f"{len(devices)} devices for the run form's pickers)",
        flush=True,
    )
    if not devices:
        print("note: 0 NVMe data disks; a TEST_DEVS run needs at least one", flush=True)
    kernel_version = _kernel_release(remote)
    print(
        f"{vm_name}: booted={system_state} blktests_ready=True "
        f"groups={len(groups)} devices={len(devices)} kernel={kernel_version}",
        flush=True,
    )
    return {
        "vm": vm_name,
        "host": system_state,
        "booted": booted,
        "blktests_ready": blktests_ready,
        "devices": devices,
        "groups": groups,
        "kernel_version": kernel_version,
    }
