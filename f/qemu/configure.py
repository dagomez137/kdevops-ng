# SPDX-License-Identifier: copyleft-next-0.3.1
"""Configure an out-of-tree QEMU build.

QEMU builds out-of-tree: `configure` lives in the source checkout but is invoked
from a separate build dir, so all generated files land under `build/` and the
source stays clean. First `meson subprojects download` (run in the SOURCE dir)
fetches QEMU's vendored meson subprojects up front; then `{worktree}/configure`
(run in the BUILD dir) writes the build.ninja for the requested targets, with
`--disable-download` so the build never reaches the network. `--prefix={destdir}`
fixes the install root so `make install` populates a stable, user-writable destdir.

The compiler is pinned through QEMU's own `--cc`/`--cxx`, not the `CC` env: the
build devShell exports `CC=clang` (clang wins the cc-wrapper's slot over GCC),
which overrides any inherited `CC`. `--cc` is applied during configure's own
argument parsing, so it wins regardless. With `ccache` on, each driver takes the
documented `--cc="ccache <cc>"` form (QEMU word-splits it into the meson compiler
array) and a managed ccache.conf is written (the devShell points CCACHE_CONFIGPATH
at it). For clang, `-Qunused-arguments` silences the spurious warning clang emits
on link steps for the devShell's GCC-oriented `-Wa,--compress-debug-sections`
(GCC, the default, never warns). Runs inside the nixos-flake build devShell, which
provides both toolchains plus meson, ninja, pkg-config, glib and pixman
(`inputsFrom = [ pkgs.qemu ]`).

The target list is comma-joined into a single `--target-list=` argv element
(QEMU normalizes the commas to spaces) so multiple targets never word-split.

Equivalent bash, run inside the nixos-flake build devShell:

    ( cd "$worktree" && meson subprojects download )
    ( cd "$build_dir" && "$worktree/configure" \
        --target-list="x86_64-softmmu,aarch64-softmmu" \
        --prefix="$destdir" \
        --cc="ccache gcc" --cxx="ccache g++" \
        --disable-download \
        $configure_args )
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from f.common.devshell import DevShell, write_ccache_conf

# Map the compiler choice to its C and C++ driver names on the devShell PATH.
_TOOLCHAIN = {"gcc": ("gcc", "g++"), "clang": ("clang", "clang++")}


def main(
    worktree: str,
    build_dir: str,
    destdir: str,
    target_list: list[str] | None = None,
    compiler: str = "gcc",
    ccache: bool = True,
    ccache_max_size: int = 10,
    configure_args: str = "",
) -> dict:
    if compiler not in _TOOLCHAIN:
        raise ValueError(f"compiler must be gcc or clang, got {compiler!r}")
    base_cc, base_cxx = _TOOLCHAIN[compiler]

    # target_list is a native list of QEMU targets; empty falls back to a single
    # x86_64 softmmu target.
    targets = target_list or ["x86_64-softmmu"]

    # ccache wraps each driver (--cc="ccache <cc>") and a managed ccache.conf is
    # written; the devShell points CCACHE_CONFIGPATH at it.
    cc, cxx = base_cc, base_cxx
    ccache_conf = None
    if ccache:
        cc = f"ccache {base_cc}"
        cxx = f"ccache {base_cxx}"
        ccache_conf = write_ccache_conf(ccache_max_size)
        print(f"ccache config: {ccache_conf}", flush=True)

    # clang sees the devShell's GCC-oriented -Wa,--compress-debug-sections (from
    # NIX_CFLAGS) as unused on link steps; -Qunused-arguments silences it.
    quiet_args = (
        ["--extra-cflags=-Qunused-arguments", "--extra-ldflags=-Qunused-arguments"]
        if compiler == "clang"
        else []
    )

    workers = Path(os.environ["WORKERS_DIR"])

    # configure_args is a space-separated string of extra --enable-*/--disable-*;
    # split into argv elements rather than concatenating.
    extra_args = shlex.split(configure_args)

    shell = DevShell(workers, "build-qemu")
    # Fetch vendored meson subprojects in the source dir (configure --disable-download
    # then resolves them locally instead of reaching the network).
    shell.run("meson", "subprojects", "download", cwd=worktree)
    # Out-of-tree configure: the script lives in the source, but runs in the build dir.
    # --cc/--cxx pin the toolchain (env CC is overridden by the devShell); the targets
    # are comma-joined into one argv element (QEMU normalizes commas to spaces).
    shell.run(
        f"{worktree}/configure",
        f"--target-list={','.join(targets)}",
        f"--prefix={destdir}",
        f"--cc={cc}",
        f"--cxx={cxx}",
        *quiet_args,
        "--disable-download",
        *extra_args,
        cwd=build_dir,
    )

    print(f"configured qemu build at {build_dir} (targets={','.join(targets)}, cc={cc})",
          flush=True)
    return {"build_dir": build_dir, "destdir": destdir, "target_list": targets,
            "compiler": compiler, "ccache_conf": ccache_conf}
