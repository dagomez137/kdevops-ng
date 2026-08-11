# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fetch the kernel devel layer onto a worktree and regenerate its source indexes.

The consumer-side companion to `f/kernel/publish_devel`, and the devel-layer analog of
`f/kernel/fetch_identity`. Resolve the `kernel-devel-<release>` store path, materialize
the build dir's developer subset (the `.cmd` command database, the generated headers and
sources, and the kconfig files a Rust index run reads; every compiled output, and the
host-tool `scripts/` and `tools/` trees, are excluded at publish), then regenerate both
source indexes locally so each names this worktree's own source: `compile_commands.json`
from `gen_compile_commands.py` for clangd, and `rust-project.json` from the
`rust-analyzer` target of `scripts/Makefile.build`, entered as a sub-make rather than
through the top-level goal (see `notes/adr/0013-rust-index-regenerated-on-the-consumer`).

`synced` says whether the worktree actually carries the commit this build produced. The
build flow's tail passes through what `f/workbench/worktree/init` reports, and that step
leaves a tree it cannot move without discarding the developer's work exactly where it is.
When it is false the `.cmd` database describes a different source than the worktree holds,
so both index halves decline together rather than name the developer's files with the
build's commands: nothing is materialized and `fetched` comes back false. It defaults on,
so a standalone call is unaffected.

The Rust half runs in the `build-kernel` devShell for its pinned `rustc`, while the
`nix copy` and the C index keep the `transfer` shell. It gates on its inputs being
present, never on an exit status, because both known missing-input cases exit 0 and
write a plausible but wrong index. A layer without `auto.conf` or `rustc_cfg`, a kernel
without `CONFIG_RUST=y`, and a kernel too old to carry the generator each print their
reason and leave `rust_project` unset: a missing Rust index must not cost the developer
their C index, so this half never raises.

The three proc-macro dylibs the index names are compiled here rather than carried in the
layer, because they are rustc-compiled output the layer deliberately holds out (see the
same ADR). `proc_macros` therefore defaults on: without the dylibs rust-analyzer does not
merely leave `module!`, `#[vtable]`, `#[kunit_tests]` and `pin_init!` unexpanded, it
raises a `macro-error` at every one of those sites, and a developer worktree exists to
give an editor that works. The build is skipped rather than fatal when a clang kernel
offers no `make_flags` to pass (`LLVM=1` alone dies on `-nostdlibinc`, see
`f/kernel/build_flags`) and when it exits nonzero; the index is written either way, and
the resolved/total dylib count in its line says which happened.

The written index is then read back and every distinct store path it names is GC-rooted
under `SYSTEM_DIR/toolchain-roots`. The index points at paths it does not own, and the
Rust library source has no root of its own: the devShell exposes it as the
`RUST_LIB_SRC` environment variable rather than as a package, so it never enters the
shell's PATH closure and `nix store gc` collects it out from under every kernel Rust
index on the host. Rooting prints its reason and continues rather than raising, like the
rest of the Rust half, since the index on disk stays usable until the next collection.

Same-host leaves `remote`/`remote_index` empty and resolves the layer from the local
index. Cross-host sets `remote` to an ssh host and `remote_index` to that builder's
`store-index` directory, and `store.resolve` reads the peer's index entry over ssh to
learn the store path, pulls it with `nix copy`, and indexes it locally. `build_dir`
defaults to the worktree's own `build` child and must stay under it.

Equivalent bash, run inside the nixos-flake transfer devShell for the cross-host half:

    sp=$(ssh "$remote" readlink "$remote_index"/kernel-devel-"$uts_release")
    nix copy --from ssh://"$remote" "$sp" --no-check-sigs
    nix build "$sp" --out-link "$index"/kernel-devel-"$uts_release"
    cp --recursive --force "$sp"/. "$worktree/build"/
    chmod --recursive u+w "$worktree/build"
    python3 "$worktree/scripts/clang-tools/gen_compile_commands.py" \\
        --directory "$worktree/build" --output "$worktree/compile_commands.json"

and the Rust half, inside the build-kernel devShell: the proc-macro dylibs first, then
the index, from the build dir the recipe redirects into:

    make --directory="$worktree" O="$worktree/build" --jobs="$(nproc)" $make_flags rust/
    cd "$worktree/build"
    make --silent --file="$worktree/scripts/Makefile.build" obj=rust rust-analyzer \\
        srcroot="$worktree" srctree="$worktree" objtree="$worktree/build" RUSTC=rustc
    cp "$worktree/build/rust-project.json" "$worktree/rust-project.json"

and last the toolchain store paths that index names, which nothing else roots:

    jq --raw-output '[.sysroot, (.crates[].root_module)]
        | map(select(startswith("/nix/store/")) | split("/")[:4] | join("/"))
        | unique[]' "$worktree/rust-project.json" |
    while read -r sp; do
        nix build "$sp" --out-link "$roots/${sp#/nix/store/*-}"
    done
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from f.common import store
from f.common.devshell import DevShell, Nix, run_logged


def main(
    worktree: str,
    uts_release: str,
    remote: str = "",
    remote_index: str = "",
    build_dir: str = "",
    required: bool = False,
    proc_macros: bool = True,
    make_flags: str = "",
    synced: bool = True,
) -> dict:
    wt = Path(worktree)
    gen = wt / "scripts/clang-tools/gen_compile_commands.py"
    if not gen.is_file():
        raise FileNotFoundError(f"no kernel source checkout at {wt}")
    build = Path(build_dir) if build_dir else wt / "build"
    if wt.resolve() not in build.resolve().parents:
        raise ValueError(
            f"build_dir {build} must live under the worktree {wt}: the fetched .cmd "
            "source paths are relative to the build dir, so only a child resolves them"
        )
    build.mkdir(parents=True, exist_ok=True)

    if not synced:
        print(
            f"worktree {wt} was left at its own commit, not this build's; not indexing "
            "it, both indexes would describe a different source than they name",
            flush=True,
        )
        return {
            "fetched": False,
            "worktree": str(wt),
            "build_dir": str(build),
            "uts_release": uts_release,
        }

    workers = Path(os.environ["WORKERS_DIR"])
    name = f"kernel-devel-{uts_release}"

    sp = store.resolve(name, workers, remote, remote_index)
    if sp is None:
        if required:
            raise FileNotFoundError(
                f"devel layer {name} not found locally or on the peer; a worktree "
                "asked to be indexed cannot be, so this is a failure rather than a "
                "bare checkout. Build this identity with reuse off to publish it."
            )
        print(
            f"devel layer {uts_release}: not found locally or on the peer", flush=True
        )
        return {
            "fetched": False,
            "worktree": str(wt),
            "build_dir": str(build),
            "uts_release": uts_release,
        }

    run_logged(
        ["cp", "--recursive", "--force", f"{sp.rstrip('/')}/.", str(build) + "/"]
    )
    run_logged(["chmod", "--recursive", "u+w", str(build)])
    print(f"materialized devel layer {sp} -> {build}", flush=True)

    cc = wt / "compile_commands.json"
    shell = DevShell(workers, "transfer")
    shell.run("python3", str(gen), "--directory", str(build), "--output", str(cc))
    entries = len(json.loads(cc.read_text())) if cc.is_file() else 0
    print(f"wrote {cc} ({entries} entries)", flush=True)

    indexed = _rust_index(wt, build, workers, proc_macros, make_flags)

    return {
        "fetched": True,
        "worktree": str(wt),
        "build_dir": str(build),
        "compile_commands": str(cc),
        "entries": entries,
        "uts_release": uts_release,
        "store_path": sp,
        "remote": remote or None,
        **indexed,
    }


def _rust_index(
    worktree: Path, build: Path, workers: Path, proc_macros: bool, make_flags: str
) -> dict:
    """Regenerate `rust-project.json` for this worktree, or report why it cannot be.

    Returns the written index and its crate count, or `None` and 0 when the half
    declined: the C index is written by this point, and returning it matters more than
    failing the step.

    The dylibs are compiled before the index so the index's own resolved/total line
    reports the post-build count.
    """
    blocked = _rust_blocker(build, worktree)
    if blocked:
        print(f"rust index: {blocked}", flush=True)
        return {"rust_project": None, "crates": 0}

    shell = DevShell(workers)
    _proc_macros(shell, worktree, build, proc_macros, make_flags)

    makefile = worktree / "scripts/Makefile.build"
    rc = shell.run(
        "make",
        "--silent",
        f"--file={makefile}",
        "obj=rust",
        "rust-analyzer",
        f"srcroot={worktree}",
        f"srctree={worktree}",
        f"objtree={build}",
        "RUSTC=rustc",
        cwd=str(build),
        check=False,
    )
    if rc != 0:
        print(f"rust index: the sub-make exited {rc}", flush=True)
        return {"rust_project": None, "crates": 0}

    # The recipe redirects into make's own cwd, so the index lands in the build dir.
    src = build / "rust-project.json"
    if not src.is_file():
        print(f"rust index: the sub-make wrote no {src}", flush=True)
        return {"rust_project": None, "crates": 0}

    dst = worktree / "rust-project.json"
    _write(dst, src.read_text())
    crates, resolved, dylibs = _index_counts(dst)
    print(
        f"wrote {dst} ({crates} crates, {resolved}/{dylibs} proc-macro dylibs present)",
        flush=True,
    )
    if dylibs and not resolved:
        print(
            "no proc-macro dylib is present, so rust-analyzer raises a `macro-error` "
            "at every `module!`, `#[vtable]`, `#[pin_data]` and `pin_init!` site",
            flush=True,
        )
    _root_toolchain(dst)
    return {"rust_project": str(dst), "crates": crates}


def _root_toolchain(index: Path) -> None:
    """GC-root the toolchain store paths `index` names, or print why one could not be.

    Each root is named for its store path minus the hash, so a toolchain bump replaces
    the root it supersedes instead of pinning the old closure forever. A failure to root
    is printed and stepped over, never raised: the index is already on disk and stays
    resolvable until the next collection.
    """
    paths = _toolchain_paths(index)
    if not paths:
        return
    try:
        roots = store.toolchain_roots_dir()
    except Exception as exc:
        print(f"toolchain roots: {exc}", flush=True)
        return
    nix, rooted = Nix(), 0
    for sp in paths:
        link = roots / Path(sp).name.split("-", 1)[-1]
        try:
            nix.run("build", sp, "--out-link", str(link))
            rooted += 1
        except Exception as exc:
            print(f"toolchain roots: {sp} ({exc})", flush=True)
    print(f"rooted {rooted}/{len(paths)} toolchain paths under {roots}", flush=True)


def _toolchain_paths(index: Path) -> list[str]:
    """The distinct top-level store paths an index names, sorted.

    The `sysroot` plus every store-rooted `root_module`, since the sysroot crates read
    their source straight out of the store. A `root_module` names a file inside the
    store path, so only its first three components identify what to root; a crate rooted
    in the worktree names no store path at all and is skipped.
    """
    data = json.loads(index.read_text())
    named = [
        data.get("sysroot"),
        *(c.get("root_module") for c in data.get("crates", [])),
    ]
    roots = set()
    for path in named:
        parts = (path or "").split("/")
        if parts[:3] == ["", "nix", "store"] and len(parts) > 3 and parts[3]:
            roots.add("/".join(parts[:4]))
    return sorted(roots)


def _proc_macros(
    shell: DevShell, worktree: Path, build: Path, proc_macros: bool, make_flags: str
) -> None:
    """Compile the three proc-macro dylibs the index names, or print why not.

    A skip and never a raise, like the rest of the Rust half: the index is the
    deliverable, and the dylibs only widen what expands inside it.
    """
    blocked = _dylib_blocker(build, proc_macros, make_flags)
    if blocked:
        print(f"proc-macro dylibs: {blocked}", flush=True)
        return
    argv = _dylib_argv(worktree, build, make_flags, len(os.sched_getaffinity(0)))
    rc = shell.run(*argv, check=False)
    if rc != 0:
        print(f"proc-macro dylibs: the build exited {rc}", flush=True)


def _dylib_blocker(build: Path, proc_macros: bool, make_flags: str) -> str | None:
    """Return why the proc-macro dylibs cannot be built here, or None when they can.

    A clang kernel needs the builder's whole flag set, not just `LLVM=1`: the wrapper's
    `-nostdlibinc` trips `-Werror,-Wunused-command-line-argument` (see
    `f/kernel/build_flags`). With nothing to pass, that run is the measured failure
    path, so it is declined up front rather than compiled into a failure.
    """
    if not proc_macros:
        return "not requested"
    if make_flags.strip():
        return None
    for name in ("include/config/auto.conf", ".config"):
        path = build / name
        if path.is_file() and "CONFIG_CC_IS_CLANG=y\n" in path.read_text():
            return f"{path} says CONFIG_CC_IS_CLANG=y and make_flags is empty"
    return None


def _dylib_argv(worktree: Path, build: Path, make_flags: str, jobs: int) -> list[str]:
    """The dylib build's argv. This goal takes `--directory`, unlike the index sub-make.

    `make_flags` arrives as one shell-quoted string (`f/kernel/build_flags` joins it),
    so it splits the way a shell would: `CC="ccache /nix/store/...clang"` is one token.
    """
    return [
        "make",
        f"--directory={worktree}",
        f"O={build}",
        f"--jobs={jobs}",
        *shlex.split(make_flags),
        "rust/",
    ]


def _rust_blocker(build: Path, worktree: Path) -> str | None:
    """Return why the Rust index cannot be regenerated here, or None when it can.

    A presence check on the generator's inputs rather than a verdict on a run: both
    known missing-input failures exit 0 and write a plausible but wrong index, so the
    only honest gate sits upstream of the sub-make. `CONFIG_RUST` is read from
    `auto.conf`, which the sub-make needs anyway, so this path never opens `.config`.
    """
    auto = build / "include/config/auto.conf"
    if not auto.is_file():
        return f"no {auto}; this devel layer predates the Rust index inputs"
    cfg = build / "include/generated/rustc_cfg"
    if not cfg.is_file():
        return f"no {cfg}; the generator's one objtree input"
    if "CONFIG_RUST=y\n" not in auto.read_text():
        return "CONFIG_RUST not enabled; skipping rust-analyzer"
    gen = worktree / "scripts/generate_rust_analyzer.py"
    if not gen.is_file():
        return f"no {gen}; this kernel carries no index generator"
    return None


def _index_counts(index: Path) -> tuple[int, int, int]:
    """Return an index's crate count and its resolved and total proc-macro dylibs.

    `scripts/generate_rust_analyzer.py` writes `proc_macro_dylib_path` as a bare path
    join with no stat, so a partial index reads like a complete one unless the paths
    are checked here.
    """
    crates = json.loads(index.read_text()).get("crates", [])
    dylibs = [
        c["proc_macro_dylib_path"] for c in crates if c.get("proc_macro_dylib_path")
    ]
    return len(crates), sum(1 for p in dylibs if Path(p).is_file()), len(dylibs)


def _write(path: Path, text: str) -> None:
    """Write through a sibling temp file so a live rust-analyzer reads no half file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
