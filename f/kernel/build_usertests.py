# SPDX-License-Identifier: copyleft-next-0.3.1
"""Build the kernel's userspace test harnesses and stage their binaries.

The harnesses under tools/testing (radix-tree, vma, memblock,
scatterlist) compile kernel source (lib/xarray.c, mm/vma.c, ...) into ordinary
userspace binaries with a bare `make` in each directory: no .config, no
`make headers`, no O= (objects land in-tree). `CATALOG` maps each harness
directory to the binaries its make produces; `harnesses` selects the
directories (default all). A binary the make did not produce, or produced
non-executable, is a hard error, never a silent drop from the stage.

Each expected binary is staged (mode preserved) to
`<destdir>/usertests_install/<dir>/<binary>`, then the harness directory is
`make clean`ed: the builds litter the shared worker worktree, and
scatterlist ships no .gitignore, so only a clean leaves the tree pristine.
`<stage>/MANIFEST` records one line per staged `<dir>/<binary>` plus the
userspace divergence knobs (`SHIFT=3`, `VMA_FLAG_BITS=128`) and the
`uts_release` the stage was built from.

The harnesses test the source tree, so the artifact keeps the build's source
identity: skips the build when the flow's reuse gate says this identity's
stage is already published (`usertests-<uts_release>` in the store index),
mirroring the compile/install reuse skip.

Equivalent bash, run inside the nixos-flake build-usertests devShell:

    make --directory="$worktree"/tools/testing/radix-tree --jobs="$(nproc)"
    make --directory="$worktree"/tools/testing/radix-tree clean
    ... (one build + clean per selected harness, binaries copied in between)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from f.common import store
from f.common.devshell import DevShell

# Harness directory under tools/testing -> the binaries its bare make produces.
CATALOG = {
    "radix-tree": ["main", "xarray", "maple", "idr-test", "multiorder"],
    "vma": ["vma"],
    "memblock": ["main"],
    "scatterlist": ["main"],
}


def _effective_harnesses(harnesses: list[str] | None) -> list[str]:
    combined = [h for h in (harnesses or list(CATALOG)) if h]
    unknown = [h for h in combined if h not in CATALOG]
    if unknown:
        raise ValueError(
            f"unknown usertests harness(es): {' '.join(unknown)} "
            f"(known: {' '.join(CATALOG)})"
        )
    return list(dict.fromkeys(combined))


def main(
    worktree: str,
    build_dir: str,
    destdir: str = "",
    harnesses: list[str] | None = None,
    reuse_present: bool = False,
    uts_release: str = "",
) -> dict:
    if reuse_present and uts_release:
        name = f"usertests-{uts_release}"
        sp = store.local_path(name)
        if sp:
            print(f"reuse: {name} already published -> {sp}", flush=True)
            return {"install_dir": "", "reused": True, "name": name, "store_path": sp}

    workers = Path(os.environ["WORKERS_DIR"])
    eff = _effective_harnesses(harnesses)

    # Stage destination is separate from the source tree; default to the
    # slot-level destdir alongside the source worktree, like install/install_modules.
    dest = Path(destdir) if destdir else Path(worktree).parent / "destdir"
    stage = dest / "usertests_install"
    stage.mkdir(parents=True, exist_ok=True)

    shell = DevShell(workers, "build-usertests")
    jobs = len(os.sched_getaffinity(0))
    staged: list[str] = []
    for h in eff:
        hdir = Path(worktree) / "tools/testing" / h
        shell.run("make", f"--directory={hdir}", f"--jobs={jobs}")
        out = stage / h
        out.mkdir(parents=True, exist_ok=True)
        for binary in CATALOG[h]:
            built = hdir / binary
            if not (built.is_file() and os.access(built, os.X_OK)):
                raise RuntimeError(
                    f"usertests harness {h} incomplete: "
                    f"{built} missing or not executable"
                )
            dst = out / binary
            shutil.copy2(built, dst)
            print(f"copied {built} -> {dst}", flush=True)
            staged.append(f"{h}/{binary}")
        shell.run("make", f"--directory={hdir}", "clean")

    manifest = stage / "MANIFEST"
    manifest.write_text(
        "".join(
            f"{line}\n"
            for line in [
                *staged,
                "SHIFT=3",
                "VMA_FLAG_BITS=128",
                f"uts_release={uts_release}",
            ]
        )
    )
    print(f"wrote {manifest}", flush=True)

    print(f"staged usertests -> {stage}", flush=True)
    print(f"{len(staged)} binaries across {len(eff)} harnesses", flush=True)
    return {"install_dir": str(stage), "harnesses": staged, "reused": False}
