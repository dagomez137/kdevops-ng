# SPDX-License-Identifier: copyleft-next-0.3.1
"""Install the built QEMU into the prefix destdir.

`make install` honors the `--prefix={destdir}` set at configure time, so it populates
`destdir/bin` (the `qemu-system-<arch>` emulators) and `destdir/share/qemu` (the data
dir QEMU resolves relative to that prefix) directly. No DESTDIR and no sudo: the
destdir is user-writable under WORKERS_DIR.

Runs inside the nixos-flake build devShell.

Equivalent bash, run inside the nixos-flake build devShell:

    make --directory="$build_dir" install
"""

from __future__ import annotations

import os
from glob import glob
from pathlib import Path

from f.common.devshell import DevShell


def main(build_dir: str, destdir: str, target_list: list[str] | None = None) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])

    shell = DevShell(workers, "build-qemu")
    shell.run("make", f"--directory={build_dir}", "install")

    # Resolve the installed emulators under the prefix's bin dir.
    binaries = sorted(glob(str(Path(destdir) / "bin" / "qemu-system-*")))
    qemu_binary = binaries[0] if binaries else None

    if binaries:
        print(
            f"installed {len(binaries)} qemu emulator(s) -> {Path(destdir) / 'bin'}",
            flush=True,
        )
        for path in binaries:
            print(f"  {path}", flush=True)
    else:
        print(
            f"no qemu-system-* binaries found under {Path(destdir) / 'bin'}", flush=True
        )

    return {
        "destdir": destdir,
        "qemu_binary": qemu_binary,
        "qemu_binaries": binaries,
        "target_list": target_list or [],
    }
