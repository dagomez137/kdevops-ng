# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Shared library for the f/kvcache/* steps (KV cache workload orchestration).
# Not a runnable step; imported by the steps as f.kvcache.common.
#
# The workload runs against one of two targets, selected per step by `target`:
#   * "vm" (the default): a booted guest carrying the kvcache test suite
#     (vendor/nixos-flake/modules/testSuites/kvcache.nix). Guest mount
#     /var/lib/kvcache (GUEST_STATE_DIR), share tag `kvcache`, host side
#     $WORKERS_DIR/shared/kvcache/<vm_name> (share_dir); units driven over
#     vsock-SSH through RemoteSystemd.
#   * "local": the worker's own host (a bare-metal GPU box such as a DGX
#     Spark, attached as a tagged remote worker; its unified-memory GPU has
#     no PCI function to pass through, so no guest is possible). The same
#     three units run under the host's `systemd --user` manager, installed
#     per deploy/spark/README.md; env files and results live under
#     $XDG_STATE_HOME/kvcache (local_state_dir), the same layout the units'
#     %S/kvcache paths resolve to. Driven through LocalSystemd below.
# Either way everything runnable is a systemd unit; these steps only start
# units and read their state.
from __future__ import annotations

import os
import shlex
import tempfile
from pathlib import Path

from f.common.devshell import DevShell, Systemd

# Re-exported so `from f.kvcache.common import RemoteSystemd, list_vms` mirrors
# the fstests/blktests import shape.
from f.common.remote import RemoteSystemd as RemoteSystemd
from f.common.remote import list_vms as list_vms

TARGETS = ["vm", "local"]

GUEST_STATE_DIR = "/var/lib/kvcache"
GUEST_TAG = "kvcache"

# Keep in sync with PRESETS in vendor/nixos-flake/pkgs/kvcache-bench/. The
# picker cache written by discover carries whatever the guest's packaged
# replayer actually advertises; this list is only the pre-discover fallback.
PRESETS = [
    "swebench-realistic",
    "swebench-burst",
    "gaia-realistic",
    "gaia-burst",
    "wildclaw-heavy",
    "max-pressure",
]

SERVE_ENV = "vllm-serve.env"


def _workers() -> Path:
    return Path(os.environ["WORKERS_DIR"])


class LocalSystemd:
    """RemoteSystemd's step-facing surface against the worker host's own manager.

    Same method names and shapes as RemoteSystemd (`ssh` runs the argv on this
    host through the `#systemd` devShell, logged by the runner itself), so a
    step drives either target through one variable. `journal_combined` reads
    the user journal's `_SYSTEMD_USER_UNIT=` stream; the kernel ring stays out,
    since reading it needs the system journal and says nothing about a
    user-unit workload on the worker's own host.
    """

    def __init__(self, workers: Path) -> None:
        self._sd = Systemd(workers)
        self._shell = DevShell(workers, shell="systemd")

    def ssh(self, *args: str, capture: bool = True, check: bool = True, **_: object):
        print(f"+ local {shlex.join(args)}", flush=True)
        if capture:
            return self._shell.capture(*args, check=check, quiet=True)
        return self._shell.run(*args, check=check, quiet=True)

    def systemctl(
        self, *args: str, capture: bool = False, check: bool = True, **_: object
    ):
        return self._sd.systemctl(*args, capture=capture, check=check)

    def show(self, unit: str, *props: str) -> dict[str, str]:
        flags = [f"--property={p}" for p in props]
        out = self.systemctl("show", unit, *flags, capture=True, check=True) or ""
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    def is_system_running(self, quiet: bool = False) -> str:
        return (
            self.systemctl("is-system-running", capture=True, check=False) or ""
        ).strip()

    def unit_exists(self, template: str) -> bool:
        out = (
            self.systemctl(
                "list-unit-files", template, "--no-legend", capture=True, check=False
            )
            or ""
        )
        return any(
            line.split() and line.split()[0] == template for line in out.splitlines()
        )

    def journal_combined(
        self, unit: str, cursor: str | None = None
    ) -> tuple[str | None, str]:
        args = ["--no-pager", "--output=short-precise", "--show-cursor"]
        args += [f"--after-cursor={cursor}"] if cursor else ["--boot"]
        args += [f"_SYSTEMD_USER_UNIT={unit}"]
        out = self._sd.journalctl(*args, capture=True, check=False) or ""
        next_cursor, body = cursor, []
        for line in out.splitlines():
            if line.startswith("-- cursor:"):
                next_cursor = line.split(":", 1)[1].strip()
            elif line.startswith("-- ") and line.endswith(" --"):
                continue
            else:
                body.append(line)
        return next_cursor, "\n".join(body)


def manager(workers: Path, vm_name: str, target: str = "vm"):
    """The systemd handle for `target`: the guest's manager or this host's own."""
    if target not in TARGETS:
        raise ValueError(f"target {target!r}: expected one of {TARGETS}")
    if target == "local":
        return LocalSystemd(workers)
    return RemoteSystemd(workers, vm_name)


def local_state_dir() -> Path:
    """The local target's state root: what the units' `%S/kvcache` resolves to."""
    base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state"))
    return base / "kvcache"


def workload_dir(vm_name: str, target: str = "vm", workers: Path | None = None) -> Path:
    """Where this target's env files and results live, host-side."""
    if target == "local":
        return local_state_dir()
    return share_dir(vm_name, workers)


def share_dir(vm_name: str, workers: Path | None = None) -> Path:
    """Host path of the VM's `kvcache` virtiofs share, name-escape hardened.

    `$WORKERS_DIR/shared/kvcache/<vm_name>`. Lives under `shared/` so every
    worker sees the same bytes the guest's virtiofsd serves. `vm_name` is
    resolved and checked to sit directly under the share root, so a crafted
    name (`../x`) can never write outside it.
    """
    root = (workers or _workers()) / "shared/kvcache"
    path = (root / vm_name).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"vm_name {vm_name!r} resolves outside {root}")
    return path


def _safe_kver(kernel_version: str) -> str:
    """Validate a `uname -r` string is a single path component (no `/`, no `..`)."""
    kv = (kernel_version or "").strip()
    if not kv or "/" in kv or kv in (".", ".."):
        raise ValueError(
            f"kernel_version {kernel_version!r} is not a safe path segment"
        )
    return kv


def _safe_preset(preset: str) -> str:
    """A preset is a systemd instance name and a path segment; keep it plain."""
    p = (preset or "").strip()
    if not p or any(c in p for c in "/\\ \t") or p in (".", ".."):
        raise ValueError(f"preset {preset!r} is not a safe instance name")
    return p


def results_dir(
    vm_name: str,
    kernel_version: str,
    preset: str,
    workers: Path | None = None,
    target: str = "vm",
) -> Path:
    """`<workload_dir>/<kver>/results/<preset>`: the host view of one run's output.

    Mirrors the unit's `--output <stateDir>/%v/results/%i`, so results from
    the same workload run under different kernels never collide.
    """
    kv = _safe_kver(kernel_version)
    p = _safe_preset(preset)
    base = (workload_dir(vm_name, target, workers) / kv / "results").resolve()
    out = (base / p).resolve()
    if base not in out.parents:
        raise ValueError(f"preset {preset!r} resolves outside {base}")
    return out


def presets_cache(
    vm_name: str, workers: Path | None = None, target: str = "vm"
) -> Path:
    """Per-target picker cache the run form's preset dropdown reads."""
    return workload_dir(vm_name, target, workers) / ".presets.json"


def _atomic_write(path: Path, data: str, mode: int = 0o644) -> None:
    """Write via a hidden temp file + rename so a concurrent reader on the shared
    dir (the guest's virtiofsd) never sees a half-written env/config file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def env_file(pairs: dict[str, str]) -> str:
    """Render a systemd EnvironmentFile body.

    systemd's EnvironmentFile parser is not a shell: it strips unescaped
    quotes of either kind anywhere in the value, so a single-quoted wrapper
    does not protect embedded JSON (the engine then receives
    `{kv_connector:...}` and rejects it). The one form that round-trips is
    a double-quoted value with backslash-escaped embedded quotes and
    backslashes, which systemd reduces to the literal text; word-splitting
    still happens only where a unit reads an unquoted `$VAR` on purpose
    (VLLM_SERVE_ARGS), and the JSON carries no spaces so it stays one
    argument."""
    lines = []
    for k, v in pairs.items():
        vv = str(v).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{k}="{vv}"')
    return "\n".join(lines) + "\n"


def main():
    """Library module imported by the f/kvcache/* steps; not a runnable step."""
    return "f/kvcache/common: share layout, presets, env-file helpers"
