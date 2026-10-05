# SPDX-License-Identifier: copyleft-next-0.3.1
"""Write the series as patch files, the form the kernel takes patches in.

The series is the commits between the merge base of `base` and HEAD, and HEAD.
Documentation/process/submitting-patches.rst asks for that base to travel with
the patches: "If you are using `git format-patch` to generate your patches,
you can automatically include the base tree information in your submission by
using the `--base` flag", so a reviewer applies them to the same base. A
series of more than one patch gets a cover letter (`--cover-letter`), which the
checks after this one skip.

`base` defaults to the checked-out ref when a lore series was applied on top
of it, because that ref is then exactly what the series sits on. A developer
branch has no such default: name the upstream ref it forked from (a tag such
as `v7.2`, or a mirror branch such as `master`).

The Worker sandbox's `check/` is emptied first; every check after this one
writes its log there, beside `patches/`.

Equivalent host bash:

    base_commit=$(git -C "$worktree" merge-base "$base" HEAD)
    git -C "$worktree" format-patch --output-directory "$check_dir/patches" \\
        --base="$base_commit" --subject-prefix="$subject_prefix" \\
        [--cover-letter] "$base_commit"
"""

from __future__ import annotations

import shutil
from pathlib import Path

from f.common.devshell import Git
from f.common.worktree import _resolve_ref
from f.kernel.checks import check_dir, series_files


def main(
    worktree: str,
    base: str = "",
    git_ref: str = "",
    b4_series: str = "",
    subject_prefix: str = "PATCH",
) -> dict:
    base = base or (git_ref if b4_series else "")
    if not base:
        raise ValueError(
            "set base: the upstream ref the series sits on (a tag such as v7.2, "
            "or a mirror branch such as master)"
        )
    git = Git()
    base_ref, _ = _resolve_ref(git, Path(worktree), base)
    base_commit = git.capture("-C", worktree, "merge-base", base_ref, "HEAD").strip()
    commits = int(
        git.capture("-C", worktree, "rev-list", "--count", f"{base_commit}..HEAD")
    )
    if not commits:
        raise ValueError(f"no commits between {base} ({base_commit[:12]}) and HEAD")
    print(f"series: {commits} commits on {base} ({base_commit[:12]})", flush=True)

    out = check_dir()
    patches = out / "patches"
    if out.exists():
        shutil.rmtree(out)
        print(f"removed {out}", flush=True)
    patches.mkdir(parents=True)
    git.run(
        "-C",
        worktree,
        "format-patch",
        "--output-directory",
        str(patches),
        f"--base={base_commit}",
        f"--subject-prefix={subject_prefix}",
        *(["--cover-letter"] if commits > 1 else []),
        base_commit,
    )
    written = sorted(patches.glob("*.patch"))
    cover = next((p for p in written if p.name.startswith("0000-")), None)
    series = [str(p) for p in written if p != cover]
    print(f"wrote {len(written)} patch files to {patches}", flush=True)

    return {
        "dir": str(patches),
        "check_dir": str(out),
        "base": base,
        "base_commit": base_commit,
        "commits": commits,
        "patches": series,
        "cover_letter": str(cover) if cover else None,
        "files": series_files(worktree, base_commit),
    }
