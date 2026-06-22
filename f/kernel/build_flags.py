# SPDX-License-Identifier: copyleft-next-0.3.1
"""Resolve the kernel make flags from the toolchain / reproducible / ccache knobs.

Produces one properly-quoted make-flags string that every make step (configure,
compile, devtools, install) consumes, so the toolchain is consistent: the kernel
docs require the same `LLVM=` value on each make invocation when configuring and
building via distinct commands (Documentation/kbuild/llvm.rst).

  - compiler=clang -> `LLVM=1` (expands CC=clang LD=ld.lld AR=llvm-ar ...).
  - reproducible   -> `KBUILD_BUILD_TIMESTAMP` + `KBUILD_BUILD_USER=kdevops` +
    `KBUILD_BUILD_HOST=kdevops` + `LOCALVERSION=` (Documentation/kbuild/reproducible-builds.rst),
    plus one `-fdebug-prefix-map=<prefix>/=` in `KCFLAGS` and `KAFLAGS`, `<prefix>`
    being the common parent of the worktree and build dir.
  - ccache         -> `CC="ccache <cc>"` on the command line (the Makefile assigns
    CC, so an env CC would not win); a managed ccache.conf is written here (cache_dir
    + a max_size of `ccache_max_size` GiB, the only non-default settings) and the
    devShell points CCACHE_CONFIGPATH at it — no ccache settings live in env vars.

The timestamp must be a real fixed value: the kernel uses
`$(or $(KBUILD_BUILD_TIMESTAMP), $(shell date))` (init/Makefile), so an EMPTY value
falls back to the live date and is NOT reproducible. Default is the Linux
announcement date; flip `timestamp_from_commit` to tie it to the commit instead.

Equivalent bash (clang + reproducible + ccache):

    make ... LLVM=1 CC="ccache clang" \
        KBUILD_BUILD_TIMESTAMP="Sun Aug 25 20:57:08 UTC 1991" \
        KBUILD_BUILD_USER=kdevops KBUILD_BUILD_HOST=kdevops LOCALVERSION= \
        KCFLAGS=-fdebug-prefix-map=<prefix>/= KAFLAGS=-fdebug-prefix-map=<prefix>/=
"""

from __future__ import annotations

import os
import shlex

from f.common.devshell import Git, write_ccache_conf

# 1991-08-25, Linus's "just a hobby, won't be big and professional" post — a fixed,
# memorable, genuinely reproducible default (unlike an empty timestamp).
_FIXED_TIMESTAMP = "Sun Aug 25 20:57:08 UTC 1991"


def main(
    compiler: str = "gcc",
    reproducible: bool = True,
    ccache: bool = True,
    ccache_max_size: int = 10,
    timestamp_from_commit: bool = False,
    make_flags: str = "",
    worktree: str = "",
    build_dir: str = "",
    commit: str = "",
) -> dict:
    if compiler not in ("gcc", "clang"):
        raise ValueError(f"compiler must be gcc or clang, got {compiler!r}")

    parts: list[str] = []
    if compiler == "clang":
        parts.append("LLVM=1")
    ccache_conf = None
    if ccache:
        parts.append(f"CC=ccache {compiler}")
        ccache_conf = write_ccache_conf(ccache_max_size)
        print(f"ccache config: {ccache_conf}", flush=True)
    prefix_map = ""
    if reproducible:
        timestamp = _FIXED_TIMESTAMP
        if timestamp_from_commit and worktree and commit:
            timestamp = (
                Git().capture("-C", worktree, "log", "-1", "--format=%cd", commit).strip()
                or _FIXED_TIMESTAMP
            )
        parts += [
            f"KBUILD_BUILD_TIMESTAMP={timestamp}",
            "KBUILD_BUILD_USER=kdevops",
            "KBUILD_BUILD_HOST=kdevops",
            "LOCALVERSION=",
        ]
        if worktree and build_dir:
            prefix = os.path.commonpath(
                [os.path.abspath(worktree), os.path.abspath(build_dir)]
            )
            prefix_map = f"-fdebug-prefix-map={prefix}/="
            print(f"path-prefix map: {prefix}/ -> ''", flush=True)

    extra = shlex.split(make_flags) if make_flags else []
    if prefix_map:
        extra = _merge_prefix_map(extra, prefix_map, parts)

    combined = shlex.join(parts + extra)
    print(f"make flags: {combined}", flush=True)
    return {"make_flags": combined, "ccache_conf": ccache_conf}


def _merge_prefix_map(extra: list[str], prefix_map: str, parts: list[str]) -> list[str]:
    """Fold the prefix map into a user-set `KCFLAGS`/`KAFLAGS`, else emit our own."""
    merged = list(extra)
    for var in ("KCFLAGS", "KAFLAGS"):
        pfx = f"{var}="
        idx = next((i for i, tok in enumerate(merged) if tok.startswith(pfx)), None)
        if idx is None:
            parts.append(f"{var}={prefix_map}")
        else:
            value = merged[idx][len(pfx):]
            merged[idx] = f"{var}={f'{value} {prefix_map}'.strip()}"
    return merged
