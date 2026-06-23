# SPDX-License-Identifier: copyleft-next-0.3.1
"""Shared helpers for the kdevops-ng build/provisioning steps (not a runnable step).

Imported with:  from f.common.devshell import DevShell, Git, Nix, Systemd

`DevShell` runs commands inside the shared nixos-flake build devShell; `Git` runs
the flake's `git` (`#git`, resolved once); `Nix` runs the raw `nix` CLI (build,
flake lock, ...); `Systemd` drives the
host `systemd --user` manager (systemctl/machinectl/journalctl) through the
`#systemd` devShell. All build an argv list (no shell tokenizes caller values, so
paths with spaces or metacharacters can neither break nor inject) and print the
exact, copy-pasteable invocation before running it (wrapped across lines for
readability). `run_logged` runs a bare host argv with the same copy-pasteable log.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

# The nix bin prepended to a build step's PATH. Only `nix` need live here -- `git`
# is resolved from the flake (see `_resolve_git`) and other tools come from the
# devShells. Configurable via NIX_BIN so a worker on a host that installs nix
# elsewhere (e.g. NixOS, where the default profile lives under the store, not
# /nix/var/nix/profiles/default) can point it at a reachable directory.
_NIX_BIN = os.environ.get("NIX_BIN", "/nix/var/nix/profiles/default/bin")

# Enable flakes + the new CLI without depending on the worker's nix.conf, so a
# fresh container behaves the same as a configured one.
_FLAKES = ["--extra-experimental-features", "nix-command flakes"]


def _nix_env() -> dict:
    # NO_COLOR keeps the job log plain — nix (and other honouring tools) emit no ANSI
    # escapes, so the saved log is readable without a de-colouring filter.
    return {**os.environ, "PATH": f"{_NIX_BIN}{os.pathsep}{os.environ['PATH']}", "NO_COLOR": "1"}


def _log(argv: list[str], indent: int = 4, width: int = 96) -> None:
    """Print a command as copy-pasteable bash.

    Short commands stay on one line. Longer ones are wrapped with `\\` continuations
    and indented by sub-command depth: each bare word (a subcommand or target)
    deepens the indent for what follows, while flags and `VAR=value` assignments
    stay at the current level (a flag's separate value rides on the flag's line).
    The depth split is heuristic -- a boolean flag immediately followed by a
    positional may render them on one line -- but the result is always valid,
    copy-pasteable bash.
    """
    flat = shlex.join(argv)
    if len(flat) + 2 <= width:
        print("+ " + flat, flush=True)
        return

    items: list[tuple[int, str]] = []
    level, i, n = 0, 0, len(argv)
    while i < n:
        tok = argv[i]
        is_flag = tok.startswith("-")
        nxt = argv[i + 1] if i + 1 < n else None
        if is_flag and "=" not in tok and nxt is not None and not nxt.startswith("-") and "=" not in nxt:
            items.append((level, f"{shlex.quote(tok)} {shlex.quote(nxt)}"))  # flag + its value
            i += 2
        elif is_flag or "=" in tok:                                          # flag / VAR=value
            items.append((level, shlex.quote(tok)))
            i += 1
        else:                                                                # subcommand/target
            items.append((level, shlex.quote(tok)))
            level += 1
            i += 1

    last = len(items) - 1
    lines = [
        ("+ " if idx == 0 else "  " + " " * (indent * lvl)) + text + (" \\" if idx < last else "")
        for idx, (lvl, text) in enumerate(items)
    ]
    print("\n".join(lines), flush=True)


def write_ccache_conf(max_size_gib: int) -> str:
    """Write the managed ccache config and return its path.

    The config file is the single source of truth (the devShell points
    CCACHE_CONFIGPATH at it). It lists only the settings that differ from
    ccache's defaults: cache_dir (the shared cache), max_size (ccache's 5 GiB
    default thrashes on a full kernel tree), and base_dir (the workers root, so
    absolute source/build paths under it hash CWD-relative and per-worker trees
    share the cache). Everything else is the ccache default and left unset.
    """
    if max_size_gib < 1:
        raise ValueError(f"ccache_max_size must be >= 1 GiB, got {max_size_gib}")
    base = Path(os.environ["WORKERS_DIR"]).resolve()
    cache = base / "shared/ccache"
    cache.mkdir(parents=True, exist_ok=True)
    conf = cache / "ccache.conf"
    conf.write_text(
        "# kdevops-ng managed ccache config (written by the build steps).\n"
        "# Only the settings that differ from ccache's defaults are listed.\n"
        f"cache_dir = {cache}\n"
        f"max_size = {max_size_gib}.0 GiB\n"
        f"base_dir = {base}\n"
    )
    return str(conf)


def flags_to_env(make_flags: str) -> dict:
    """Parse a `KEY=VALUE ...` make-flags string into an env dict.

    For commands that run `make` indirectly and cannot take command-line make
    variables (e.g. scripts/kconfig/merge_config.sh), the same flags are passed
    through the environment instead — `LLVM=1` is the one that matters for config.
    """
    env: dict = {}
    for tok in shlex.split(make_flags):
        if "=" in tok and not tok.startswith("-"):
            key, value = tok.split("=", 1)
            env[key] = value
    return env


def run_logged(argv: list[str], capture: bool = False, check: bool = True):
    """Run a bare host argv, printing the same copy-pasteable command first.

    For host binaries that are neither a devShell nor a `nix` invocation (e.g. the
    VM's `qemu-img`, addressed by its absolute /nix/store path). `capture` returns
    stdout; otherwise streams to the job log and returns the exit code.
    """
    _log(argv)
    if capture:
        return subprocess.run(argv, env=_nix_env(), check=check, text=True,
                              stdout=subprocess.PIPE).stdout
    return subprocess.run(argv, env=_nix_env(), check=check).returncode


def main():
    """This module is a library imported by the build steps, not a runnable step."""
    return "f/common/devshell: shared DevShell/Git/Nix/Systemd helpers"


def vendor_dir(workers: Path | str | None = None) -> Path:
    """Top-level `vendor/` of the pinned vendored projects (ADR-0006).

    Exposed to workers as VENDOR_DIR (a sibling of WORKERS_DIR, bind-mounted
    read-only at the same absolute path); falls back to that sibling so a local
    `wmill script preview` outside the container still resolves it.
    """
    if os.environ.get("VENDOR_DIR"):
        return Path(os.environ["VENDOR_DIR"])
    base = Path(workers) if workers else Path(os.environ["WORKERS_DIR"])
    return base.parent / "vendor"


class DevShell:
    """Run commands in the shared nixos-flake build devShell (argv, no shell).

    `run` streams output to the job log; `capture` returns stdout. `cwd` runs the
    command from a directory (e.g. the worktree, for merge_config.sh).
    """

    def __init__(self, workers: Path, shell: str = "build-kernel") -> None:
        # Default to the lean kernel shell; qemu steps pass shell="build" for the
        # qemu-laden one (see flake.nix devShells). path: resolves the subtree as a
        # standalone flake, not a subdir of the enclosing kdevops-ng git repo.
        self._flake = f"path:{vendor_dir(workers)}/nixos-flake#{shell}"
        # Locate the managed ccache config (written by f/kernel/build_flags). All
        # ccache settings, including cache_dir, live in that file — CCACHE_CONFIGPATH
        # only points at it. Harmless when a build does not set CC="ccache ...".
        self._env = {**_nix_env(), "CCACHE_CONFIGPATH": f"{workers}/shared/ccache/ccache.conf"}

    def _argv(self, command: str, *args: str) -> list[str]:
        return ["nix", *_FLAKES, "develop", self._flake, "--command", command, *args]

    def run(self, command: str, *args: str, cwd: str | None = None,
            env: dict | None = None, check: bool = True, quiet: bool = False) -> int:
        argv = self._argv(command, *args)
        if not quiet:
            _log(argv)
        return subprocess.run(argv, env={**self._env, **(env or {})}, check=check,
                              cwd=cwd).returncode

    def capture(self, command: str, *args: str, cwd: str | None = None,
                env: dict | None = None, check: bool = True, quiet: bool = False) -> str:
        argv = self._argv(command, *args)
        if not quiet:
            _log(argv)
        return subprocess.run(argv, env={**self._env, **(env or {})}, check=check, text=True,
                              stdout=subprocess.PIPE, cwd=cwd).stdout


def _resolve_git(workers: Path | str | None = None) -> str:
    """Resolve the nixos-flake's `git` binary, gc-rooting it for reuse.

    The worker only needs `nix` on `NIX_BIN`; `git` itself comes from the flake
    (its pinned nixpkgs + overlays). The first call builds the `#git` output and
    pins it at `WORKERS_DIR/shared/gitbin` via `nix build --out-link` (a GC root),
    so later steps and other workers on the host reuse the same store path with a
    bare stat, no re-evaluation.
    """
    base = Path(workers) if workers else Path(os.environ["WORKERS_DIR"])
    link = base / "shared" / "gitbin"
    git = link / "bin" / "git"
    if not git.exists():
        Nix().run("build", f"path:{vendor_dir(base)}/nixos-flake#git", "--out-link", str(link))
    return str(git)


class Git:
    """Run the nixos-flake's `git` by argv (no shell).

    The binary is resolved from the flake's `#git` output (see `_resolve_git`), so
    the worker's `NIX_BIN` only needs `nix`. `run` streams output; `ok` returns
    whether the command succeeded; `capture` returns stdout.
    """

    def __init__(self, workers: Path | str | None = None) -> None:
        self._env = _nix_env()
        self._git = _resolve_git(workers)

    def run(self, *args: str, check: bool = True) -> int:
        argv = [self._git, *args]
        _log(argv)
        return subprocess.run(argv, env=self._env, check=check).returncode

    def ok(self, *args: str) -> bool:
        argv = [self._git, *args]
        _log(argv)
        return subprocess.run(argv, env=self._env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

    def capture(self, *args: str, check: bool = True) -> str:
        argv = [self._git, *args]
        _log(argv)
        return subprocess.run(argv, env=self._env, check=check, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout


class Nix:
    """Run the raw `nix` CLI by argv (no shell): `build`, `flake lock`, ...

    Flakes and the new CLI are enabled per-invocation, so the worker's nix.conf
    need not be configured. `run` streams output; `capture` returns stdout.
    """

    def __init__(self) -> None:
        self._env = _nix_env()

    def _argv(self, *args: str) -> list[str]:
        return ["nix", *_FLAKES, *args]

    def run(self, *args: str, cwd: str | None = None) -> None:
        argv = self._argv(*args)
        _log(argv)
        subprocess.run(argv, env=self._env, check=True, cwd=cwd)

    def capture(self, *args: str, cwd: str | None = None) -> str:
        argv = self._argv(*args)
        _log(argv)
        return subprocess.run(argv, env=self._env, check=True, text=True,
                              stdout=subprocess.PIPE, cwd=cwd).stdout

    def out_path(self, ref: str) -> str:
        """Resolve a flake ref to its built /nix/store output path."""
        return self.capture("build", "--no-link", "--print-out-paths", ref).strip().splitlines()[-1]


class Systemd:
    """Drive the host `systemd --user` manager through the `#systemd` devShell (argv).

    Wraps `DevShell(workers, shell="systemd")`: `systemctl`/`machinectl`/`journalctl`
    each prepend `--user` and delegate to `DevShell.run`/`.capture`, reusing the
    identical `nix develop <flake>#systemd --command ...` dispatch and copy-pasteable
    `_log`. `capture=True` returns stdout; otherwise streams and returns the exit code.

    From a worker container, the tools reach the host manager over the mounted D-Bus
    socket (`DBUS_SESSION_BUS_ADDRESS`). They are run with `XDG_RUNTIME_DIR` unset so
    `systemctl`/`machinectl` use that bus instead of the host manager's private control
    socket (`$XDG_RUNTIME_DIR/systemd/private`), whose connection the manager refuses
    across the rootless-podman namespaces. The units' own `%t` paths are resolved by
    the manager host-side, so unsetting it client-side does not affect them.
    """

    def __init__(self, workers: Path) -> None:
        self._shell = DevShell(workers, shell="systemd")

    def _tool(self, tool: str, *args: str, capture: bool, check: bool):
        argv = ("env", "--unset=XDG_RUNTIME_DIR", tool, "--user", *args)
        if capture:
            return self._shell.capture(*argv, check=check)
        return self._shell.run(*argv, check=check)

    def systemctl(self, *args: str, capture: bool = False, check: bool = True):
        return self._tool("systemctl", *args, capture=capture, check=check)

    def machinectl(self, *args: str, capture: bool = False, check: bool = True):
        return self._tool("machinectl", *args, capture=capture, check=check)

    def journalctl(self, *args: str, capture: bool = False, check: bool = True):
        return self._tool("journalctl", *args, capture=capture, check=check)
