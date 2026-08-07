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

The Rust half runs in the `build-kernel` devShell for its pinned `rustc`, while the
`nix copy` and the C index keep the `transfer` shell. It gates on its inputs being
present, never on an exit status, because both known missing-input cases exit 0 and
write a plausible but wrong index. A layer without `auto.conf` or `rustc_cfg`, a kernel
without `CONFIG_RUST=y`, and a kernel too old to carry the generator each print their
reason and leave `rust_project` unset: a missing Rust index must not cost the developer
their C index, so this half never raises.

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

and the Rust index, inside the build-kernel devShell, from the build dir the recipe
redirects into:

    cd "$worktree/build"
    make --silent --file="$worktree/scripts/Makefile.build" obj=rust rust-analyzer \\
        srcroot="$worktree" srctree="$worktree" objtree="$worktree/build" RUSTC=rustc
    cp "$worktree/build/rust-project.json" "$worktree/rust-project.json"
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from f.common import store
from f.common.devshell import DevShell, run_logged


def main(
    worktree: str,
    uts_release: str,
    remote: str = "",
    remote_index: str = "",
    build_dir: str = "",
    required: bool = False,
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

    indexed = _rust_index(wt, build, workers)

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


def _rust_index(worktree: Path, build: Path, workers: Path) -> dict:
    """Regenerate `rust-project.json` for this worktree, or report why it cannot be.

    Returns the written index and its crate count, or `None` and 0 when the half
    declined: the C index is written by this point, and returning it matters more than
    failing the step.
    """
    blocked = _rust_blocker(build, worktree)
    if blocked:
        print(f"rust index: {blocked}", flush=True)
        return {"rust_project": None, "crates": 0}

    makefile = worktree / "scripts/Makefile.build"
    rc = DevShell(workers).run(
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
            "the devel layer holds the proc-macro dylibs out, so `module!`, "
            "`#[vtable]`, `#[pin_data]` and `pin_init!` do not expand",
            flush=True,
        )
    return {"rust_project": str(dst), "crates": crates}


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
