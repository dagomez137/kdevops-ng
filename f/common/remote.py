# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Shared guest vsock-SSH systemd transport, imported as f.common.remote (not a
# runnable step). Drives a booted guest's systemd over the AF_VSOCK SSH
# transport from a vm worker, and lists the rendered guests. General by design:
# no fstests/kunit coupling, so f/fstests/* and f/kunit/* import it the same way.
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from f.common.devshell import DevShell, system_dir

_CID_RE = re.compile(r"^\s*HostName\s+vsock/(\d+)\s*$")

# systemd's stable journal MESSAGE_ID values (sd-messages.h). A start job
# opens with UNIT_STARTING and ends with exactly one of STARTED or FAILED;
# UNIT_STOPPED closes a stop job; each service process exit carries EXIT_STATUS
# under UNIT_PROCESS_EXIT.
MSG_UNIT_STARTING = "7d4958e842da4a758f6c1cdc7b36dcc5"
MSG_UNIT_STARTED = "39f53479d3a045ac8e11786248231fbf"
MSG_UNIT_FAILED = "be02cf6855d2428ba40df7e9d022f03d"
MSG_UNIT_STOPPED = "9d1aaa27d60140bd96365438aad20286"
MSG_UNIT_PROCESS_EXIT = "98e322203f7a4ed290d09fe03c09fe15"


def journal_message(record: dict) -> str:
    """A journal record's MESSAGE as text (json output encodes non-UTF-8 as a byte array)."""
    msg = record.get("MESSAGE", "")
    if isinstance(msg, list):
        return bytes(msg).decode("utf-8", errors="replace")
    return msg if isinstance(msg, str) else ""


class RemoteSystemd:
    """Drive a booted guest's `systemd` over vsock-SSH, from the vm worker.

    Every guest command is one explicit `ssh` argv: the options are passed on the
    command line (not hidden in a config file or a SYSTEMD_SSH wrapper), so the
    runner logs the exact, copy-pasteable invocation. `ssh` and `systemd-ssh-proxy`
    come from the `#systemd` devShell; the vsock cid is read from `f/qsu/boot`'s
    `system/ssh/config.d/<vm>.conf`, or supplied explicitly. `systemctl`/`journalctl`
    run in the guest over that `ssh`.

    Equivalent command, against the guest over vsock-SSH:

        ssh -o ProxyCommand='<proxy> %h %p' -o User=root -o IdentityFile=<key> \
            vsock/<cid> <args>
    """

    def __init__(self, workers: Path, vm_name: str, cid: int | None = None) -> None:
        self._vm = vm_name
        self._shell = DevShell(workers, shell="systemd")
        self._key = system_dir() / "ssh/id_ed25519"
        # Read OUR managed ssh config instead of the worker container's /etc/ssh/
        # ssh_config (which carries a GSSAPIAuthentication option the devShell's ssh
        # build rejects, warning on every call). -F makes the system config ignored;
        # our explicit -o below still take precedence. Fall back to /dev/null if the
        # managed config is absent (workbench not initialised).
        config = system_dir() / "ssh/config"
        self._config = str(config) if config.is_file() else "/dev/null"
        self._cid = cid if cid is not None else self._resolve_cid(vm_name)
        if self._cid is None:
            conf = system_dir() / "ssh/config.d" / f"{vm_name}.conf"
            raise ValueError(
                f"no vsock cid for {vm_name!r}: pass cid= or boot the VM so "
                f"{conf} carries HostName vsock/<cid>"
            )
        self._proxy = self._resolve_proxy()

    @staticmethod
    def _resolve_cid(vm_name: str) -> int | None:
        """Parse the vsock cid from `f/qsu/boot`'s `$SYSTEM_DIR/ssh/config.d/<vm>.conf`."""
        conf = system_dir() / "ssh/config.d" / f"{vm_name}.conf"
        if not conf.is_file():
            return None
        for line in conf.read_text().splitlines():
            m = _CID_RE.match(line)
            if m:
                return int(m.group(1))
        return None

    def _resolve_proxy(self) -> Path:
        """`systemd-ssh-proxy`, the sibling of `systemctl`'s bin in the `#systemd` devShell."""
        out = self._shell.capture("sh", "-c", "command -v systemctl").strip()
        if not out:
            raise RuntimeError("systemctl not found in the #systemd devShell")
        return Path(out).resolve().parent.parent / "lib/systemd/systemd-ssh-proxy"

    def _ssh_argv(self, *args: str) -> tuple[str, ...]:
        """The explicit `ssh` argv dialing the guest's AF_VSOCK with the kdevops key.

        `ssh` concatenates its remote-command arguments with spaces and hands the
        result to the guest's login shell *unquoted*, so passing `args` as separate
        argv lets the remote shell re-split on any metacharacter they contain (a
        journal cursor's `;`, a `bash -c` script's spaces). Instead we pre-join with
        `shlex.join` and pass the single quoted string: the remote shell parses it
        back into exactly `args`: bare tokens (`+`, `_TRANSPORT=kernel`) stay bare,
        only metacharacter-bearing tokens get quoted.
        """
        return (
            "ssh",
            "-F",
            self._config,
            "-o",
            "LogLevel=ERROR",
            "-o",
            f"ProxyCommand={self._proxy} %h %p",
            "-o",
            "ProxyUseFdpass=yes",
            "-o",
            f"IdentityFile={self._key}",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "User=root",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            f"vsock/{self._cid}",
            shlex.join(args),
        )

    def ssh(
        self,
        *args: str,
        capture: bool = True,
        check: bool = True,
        quiet: bool = False,
        merge_stderr: bool = False,
    ):
        """Run `<args>` in the guest over the vsock-SSH transport.

        Logs the terse `+ ssh <vm> <args>` (the `nix develop … --command ssh -o … -o …`
        wrapper is constant boilerplate, so the devShell dispatch is logged quietly);
        `quiet` drops even that line, for the repeated polls of a wait/reboot loop.
        `merge_stderr` folds the command's stderr into the captured output, so a tool
        that reports on stderr (for example `xfs_info` on a device with no superblock)
        is surfaced rather than dropped; capture only.
        """
        if not quiet:
            print(f"+ ssh {self._vm} {shlex.join(args)}", flush=True)
        argv = self._ssh_argv(*args)
        if capture:
            return self._shell.capture(
                *argv, check=check, quiet=True, merge_stderr=merge_stderr
            )
        return self._shell.run(*argv, check=check, quiet=True)

    def systemctl(
        self, *args: str, capture: bool = False, check: bool = True, quiet: bool = False
    ):
        """`systemctl <args>` in the guest."""
        return self.ssh("systemctl", *args, capture=capture, check=check, quiet=quiet)

    def show(self, unit: str, *props: str) -> dict[str, str]:
        """Parse `show --property=` KEY=VALUE lines into a dict (no `--value`, which drops the keys)."""
        flags = [f"--property={p}" for p in props]
        out = self.systemctl("show", unit, *flags, capture=True, check=True) or ""
        result: dict[str, str] = {}
        for line in out.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                result[key] = value
        return result

    def is_system_running(self, quiet: bool = False) -> str:
        """`systemctl is-system-running`: e.g. `running`, `degraded`. Never raises."""
        return (
            self.systemctl("is-system-running", capture=True, check=False, quiet=quiet)
            or ""
        ).strip()

    def journal_combined(
        self, unit: str, cursor: str | None = None
    ) -> tuple[str | None, str]:
        """The guest's `<unit>` journal and the kernel ring buffer, merged chronologically
        (`journalctl … _SYSTEMD_UNIT=<unit> + _TRANSPORT=kernel`), for live streaming a run.

        From this boot on the first call, then only entries past `cursor` (so a poll loop
        can print incrementally). Returns `(next_cursor, body)`: `body` is the new entries
        with journalctl's own `-- …` meta lines stripped; `next_cursor` resumes the next
        call (unchanged when nothing new). Never raises on a transient ssh failure.

        When the guest reboots mid-run the cursor's boot id is gone and journalctl prints
        "Failed to seek to cursor"; we retry once from `--boot` so the stream re-homes on
        the new boot instead of stalling, and never surface that error line as journal text.
        """

        def _query(selector: list[str]) -> str:
            args = [
                "journalctl",
                "--no-pager",
                "--output=short-precise",
                "--show-cursor",
            ]
            args += selector + [f"_SYSTEMD_UNIT={unit}", "+", "_TRANSPORT=kernel"]
            out = self.ssh(*args, check=False)
            return out if isinstance(out, str) else ""

        out = _query([f"--after-cursor={cursor}"] if cursor else ["--boot"])
        if cursor and "Failed to seek to cursor" in out:
            out = _query(["--boot"])
        next_cursor, body = cursor, []
        for line in out.splitlines():
            if line.startswith("-- cursor:"):
                next_cursor = line.split(":", 1)[1].strip()
            elif line.startswith("-- ") and line.endswith(" --"):
                continue
            elif "Failed to seek to cursor" in line:
                continue
            else:
                body.append(line)
        return next_cursor, "\n".join(body)

    def journal_cursor(self) -> str:
        """The guest journal's end-of-now cursor (`--lines=0 --show-cursor`).

        Captured before starting a unit, it bounds every later journal read to
        that one run: entries after the cursor cannot belong to a previous
        invocation, however fast the unit ran or whether systemd still has it
        loaded.
        """
        out = (
            self.ssh("journalctl", "--no-pager", "--boot", "--lines=0", "--show-cursor")
            or ""
        )
        for line in out.splitlines():
            if line.startswith("-- cursor:"):
                return line.split(":", 1)[1].strip()
        raise RuntimeError("journalctl returned no cursor")

    def journal_unit(
        self, unit: str, cursor: str | None = None, quiet: bool = True
    ) -> tuple[str | None, list[dict]]:
        """This boot's journal records for `--unit=<unit>` past `cursor`, parsed.

        `--unit=` matches both the unit's own output and PID1's lifecycle records
        about it (`UNIT=`), so the returned records carry the run's KTAP lines and
        the `MSG_UNIT_*` job outcome in one stream. Returns `(next_cursor,
        records)`; `next_cursor` resumes the next call (unchanged when nothing
        new). Raises on a failed fetch, so a poll loop can tell a transient
        transport error from an empty read.
        """
        args = [
            "journalctl",
            "--no-pager",
            "--output=json",
            "--show-cursor",
            f"--unit={unit}",
        ]
        args += [f"--after-cursor={cursor}"] if cursor else ["--boot"]
        out = self.ssh(*args, quiet=quiet) or ""
        next_cursor, records = cursor, []
        for line in out.splitlines():
            if line.startswith("-- cursor:"):
                next_cursor = line.split(":", 1)[1].strip()
                continue
            if not line.startswith("{"):
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return next_cursor, records

    def unit_exists(self, template: str) -> bool:
        """Whether the guest knows `<template>` (in-guest `list-unit-files`)."""
        out = (
            self.systemctl(
                "list-unit-files", template, "--no-legend", capture=True, check=False
            )
            or ""
        )
        return any(
            line.split() and line.split()[0] == template for line in out.splitlines()
        )


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint: all guests, from their render sidecars.

    Globs `WORKERS_DIR/shared/vm/*.vars.json` (one per rendered guest, removed on
    destroy), the same source `f/qsu/bringup` lists for reuse. Pure stdlib so the
    dynselect runtime needs no extra deps; importing `f.qsu.common.vm_options`
    here would pull in jinja2, which the dynselect lock does not carry.
    """
    d = Path(os.environ["WORKERS_DIR"]) / "shared/vm"
    vms = (
        sorted(p.name.removesuffix(".vars.json") for p in d.glob("*.vars.json"))
        if d.is_dir()
        else []
    )
    return [{"label": v, "value": v} for v in vms if filterText.lower() in v.lower()]


def main():
    """Library module imported by the f/fstests/* and f/kunit/* steps; not a step."""
    return "f/common/remote: guest vsock-SSH systemd transport + VM listing"
