# SPDX-License-Identifier: copyleft-next-0.3.1
"""Build-check the usertests harnesses at the bisect candidate commit.

The host-side payload of `f/kernel/bisect`'s `usertests_build` mode: no
guest, no boot, just whether the selected `tools/testing` harnesses compile
at the candidate. Materializes the candidate in the bisect state clone
(`$SYSTEM_DIR/bisect/<vm_name>/repo`, created by `f/kernel/bisect_step`)
through a sparse checkout of the trees the harnesses reach (`tools`,
`include`, `lib`, `mm`), then runs each harness's bare `make` in the
`nixos-flake#build-usertests` devShell, exactly as `f/kernel/build_usertests`
does for the real suite.

The verdict lands in `report.json` beside `state.json`, where `bisect_step`
reads it. Without `error_re` the verdict is the build itself: `passed` when
every make succeeds, `failed` when one fails. With `error_re` set the hunt
asks when that signature appeared, not whether the build is healthy: a
failure matching it is `failed`, a failure without it is `passed`, so a
range wrecked by an older, unrelated break can still bisect the layer the
signature names. The caveat is inherent to signature hunts: a break early
enough to stop compilation before the signature's file would read as
`passed` too.

Equivalent commands, in the state clone:

    git sparse-checkout set tools include lib mm
    git checkout --force --detach <candidate>
    git clean -ffdx -- tools/testing/<harness>
    make --directory=tools/testing/<harness> --jobs="$(nproc)"
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from f.common.devshell import DevShell, Git
from f.kernel.build_usertests import CATALOG

_SPARSE_TREES = ("tools", "include", "lib", "mm")


def main(
    vm_name: str,
    candidate: str,
    harnesses: list[str] | None = None,
    error_re: str = "",
) -> dict:
    eff = [h for h in (harnesses or list(CATALOG)) if h]
    unknown = [h for h in eff if h not in CATALOG]
    if unknown:
        raise ValueError(
            f"unknown usertests harness(es): {' '.join(unknown)} "
            f"(known: {' '.join(CATALOG)})"
        )
    if not candidate:
        raise ValueError("candidate must name a commit to build-check")

    sdir = Path(os.environ["SYSTEM_DIR"]) / "bisect" / vm_name
    repo = sdir / "repo"
    if not repo.is_dir():
        raise RuntimeError(f"no bisect state clone at {repo}; run bisect_step first")

    workers = Path(os.environ["WORKERS_DIR"])
    git = Git(workers)
    git.run("-C", str(repo), "sparse-checkout", "set", *_SPARSE_TREES)
    git.run("-C", str(repo), "checkout", "--force", "--detach", candidate)

    shell = DevShell(workers, "build-usertests")
    jobs = len(os.sched_getaffinity(0))
    failed_output: list[str] = []
    failed: list[str] = []
    for h in eff:
        hdir = repo / "tools/testing" / h
        git.run("-C", str(repo), "clean", "-ffdx", "--", f"tools/testing/{h}")
        try:
            out = shell.capture(
                "make", f"--directory={hdir}", f"--jobs={jobs}", merge_stderr=True
            )
        except subprocess.CalledProcessError as exc:
            out = exc.stdout or ""
            failed.append(h)
            failed_output.append(out)
        if out.strip():
            print(out.rstrip(), flush=True)

    matched = None
    if not failed:
        status = "passed"
    elif not error_re:
        status = "failed"
    else:
        matched = bool(re.search(error_re, "\n".join(failed_output)))
        status = "failed" if matched else "passed"

    report = {
        "status": status,
        "candidate": candidate,
        "harnesses": eff,
        "failed": failed,
        "matched": matched,
    }
    path = sdir / "report.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {path}", flush=True)
    print(f"status={status} failed={','.join(failed) or '-'}", flush=True)
    return report
