# SPDX-License-Identifier: copyleft-next-0.3.1
"""Build and install the kernel selftests into a self-contained tree.

`make kselftest-install` (the root Makefile rule, whose `headers` dependency runs
first) builds tools/testing/selftests and installs the selected collections into
`KSFT_INSTALL_PATH`. The installed tree is self-contained: `run_kselftest.sh`, the
`kselftest-list.txt` catalog (one `collection:test` line each), the `kselftest/`
helper dir, one directory per collection (with its `config` and `settings`), and a
`VERSION` stamp. `TARGETS` selects the collections; `FORCE_TARGETS=1` turns a
collection that fails to build into a hard error instead of a silent drop from the
install.

The default `TARGETS` is a curated syscall/uapi-heavy set that builds with the
`build-kselftests` devShell's userland (libcap, numactl, libmnl, fuse3, liburing);
`extra_targets` appends free-form collections on top.

Skips the build when the flow's reuse gate says this identity's kselftests tree is
already published (`kselftests-<uts_release>` in the store index), mirroring the
compile/install reuse skip.

Equivalent bash, run inside the nixos-flake build-kselftests devShell:

    make --directory="$worktree" O="$build_dir" --jobs="$(nproc)" \\
        kselftest-install KSFT_INSTALL_PATH="$destdir"/kselftest_install \\
        TARGETS="size breakpoints ..." FORCE_TARGETS=1
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from f.common import store
from f.common.devshell import DevShell

# Curated default TARGETS: syscall/uapi collections that build self-contained
# with the build-kselftests devShell userland.
DEFAULT_TARGETS = [
    "size",
    "breakpoints",
    "kcmp",
    "mincore",
    "splice",
    "sync",
    "clone3",
    "fchmodat2",
    "mount_setattr",
    "memfd",
    "mqueue",
    "syscall_user_dispatch",
    "ptrace",
    "seccomp",
    "cgroup",
    "futex",
    "rlimits",
    "capabilities",
    "exec",
    "proc",
    "sysctl",
    "lib",
    "kmod",
    "module",
    "firmware",
    "timers",
    "kselftest_harness",
]

# A collection is a tools/testing/selftests path (possibly nested, e.g.
# net/forwarding): plain path components only (no `.`/`..`), no shell or make
# metacharacters.
_TARGET_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")


def _effective_targets(targets: list[str] | None, extra_targets: str) -> list[str]:
    combined = list(targets or DEFAULT_TARGETS) + shlex.split(extra_targets)
    for t in combined:
        if (
            not t
            or not _TARGET_RE.match(t)
            or any(part.strip(".") == "" for part in t.split("/"))
        ):
            raise ValueError(
                f"invalid selftests collection {t!r}: must match {_TARGET_RE.pattern}"
            )
    return list(dict.fromkeys(combined))


def main(
    worktree: str,
    build_dir: str,
    destdir: str = "",
    targets: list[str] | None = None,
    extra_targets: str = "",
    reuse_present: bool = False,
    uts_release: str = "",
) -> dict:
    if reuse_present and uts_release:
        name = f"kselftests-{uts_release}"
        sp = store.local_path(name)
        if sp:
            print(f"reuse: {name} already published -> {sp}", flush=True)
            return {"install_dir": "", "reused": True, "name": name, "store_path": sp}

    workers = Path(os.environ["WORKERS_DIR"])
    eff = _effective_targets(targets, extra_targets)

    # Install destination is separate from the build dir; default to the slot-level
    # destdir alongside the source worktree, like install/install_modules.
    dest = Path(destdir) if destdir else Path(worktree).parent / "destdir"
    install = dest / "kselftest_install"
    install.mkdir(parents=True, exist_ok=True)

    shell = DevShell(workers, "build-kselftests")
    shell.run(
        "make",
        f"--directory={worktree}",
        f"O={build_dir}",
        f"--jobs={len(os.sched_getaffinity(0))}",
        "kselftest-install",
        f"KSFT_INSTALL_PATH={install}",
        f"TARGETS={' '.join(eff)}",
        "FORCE_TARGETS=1",
    )

    runner = install / "run_kselftest.sh"
    if not (runner.is_file() and os.access(runner, os.X_OK)):
        raise RuntimeError(
            f"kselftest install incomplete: {runner} missing or not executable"
        )
    listing = install / "kselftest-list.txt"
    tests = (
        [line for line in listing.read_text().splitlines() if line.strip()]
        if listing.is_file()
        else []
    )
    if not tests:
        raise RuntimeError(f"kselftest install incomplete: {listing} missing or empty")
    # The upstream install prints "Skipping non-existent dir" and silently drops a
    # collection it could not build; refuse a partial tree instead.
    missing = [t for t in eff if not (install / t).is_dir()]
    if missing:
        raise RuntimeError(
            f"selftests collections missing from the install: {' '.join(missing)}"
        )

    version_file = install / "VERSION"
    version = version_file.read_text().strip() if version_file.is_file() else ""

    print(f"installed kselftests -> {install}", flush=True)
    print(f"{len(tests)} tests across {len(eff)} collections", flush=True)
    return {
        "install_dir": str(install),
        "targets": eff,
        "tests": len(tests),
        "version": version,
        "reused": False,
    }
