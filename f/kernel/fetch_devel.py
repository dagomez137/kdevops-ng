# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fetch the kernel devel layer onto a worktree and regenerate its clangd index.

The consumer-side companion to `f/kernel/publish_devel`, and the devel-layer analog of
`f/kernel/fetch_identity`. Resolve the `kernel-devel-<release>` store path — the build
dir's developer subset (the `.cmd` files, generated headers, `Module.symvers`, `scripts/`
and the GDB helpers, binaries already excluded at publish) — and materialize it into this
worktree's build dir, then regenerate `compile_commands.json` locally so it indexes this
worktree's own source.

Same-host leaves `remote`/`remote_index` empty and resolves the layer from the local
index. Cross-host sets `remote` to an ssh host and `remote_index` to that builder's
`store-index` directory: read the peer's index entry over ssh to learn the store path,
pull it with `nix copy`, and index it locally. `build_dir` defaults to the worktree's own
`build` child and must stay under it.

Equivalent bash, run inside the nixos-flake transfer devShell for the cross-host half:

    sp=$(ssh "$remote" readlink "$remote_index"/kernel-devel-"$uts_release")
    nix copy --from ssh://"$remote" "$sp" --no-check-sigs
    nix build "$sp" --out-link "$index"/kernel-devel-"$uts_release"
    cp --recursive --force "$sp"/. "$worktree/build"/
    chmod --recursive u+w "$worktree/build"
    python3 "$worktree/scripts/clang-tools/gen_compile_commands.py" \\
        --directory "$worktree/build" --output "$worktree/compile_commands.json"
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
) -> dict:
    wt = Path(worktree)
    gen = wt / "scripts/clang-tools/gen_compile_commands.py"
    if not gen.is_file():
        raise FileNotFoundError(f"no kernel source checkout at {wt}")
    build = Path(build_dir) if build_dir else wt / "build"
    if wt.resolve() not in build.resolve().parents:
        raise ValueError(
            f"build_dir {build} must live under the worktree {wt}: the fetched .cmd "
            "source paths are relative to the build dir, so only a child resolves them")
    build.mkdir(parents=True, exist_ok=True)

    workers = Path(os.environ["WORKERS_DIR"])
    name = f"kernel-devel-{uts_release}"

    sp = store.local_path(name)
    if sp is None and remote and remote_index:
        sp = store.peer_path(workers, remote, remote_index, name)
        if sp is not None:
            store.fetch(workers, remote, sp)
            store.link_local(name, sp)
    if sp is None:
        print(f"devel layer {uts_release}: not found locally or on the peer",
              flush=True)
        return {
            "fetched": False,
            "worktree": str(wt),
            "build_dir": str(build),
            "uts_release": uts_release,
        }

    run_logged(["cp", "--recursive", "--force",
                f"{sp.rstrip('/')}/.", str(build) + "/"])
    run_logged(["chmod", "--recursive", "u+w", str(build)])
    print(f"materialized devel layer {sp} -> {build}", flush=True)

    cc = wt / "compile_commands.json"
    shell = DevShell(workers, "transfer")
    shell.run("python3", str(gen), "--directory", str(build), "--output", str(cc))
    entries = len(json.loads(cc.read_text())) if cc.is_file() else 0
    print(f"wrote {cc} ({entries} entries)", flush=True)

    return {
        "fetched": True,
        "worktree": str(wt),
        "build_dir": str(build),
        "compile_commands": str(cc),
        "entries": entries,
        "uts_release": uts_release,
        "store_path": sp,
        "remote": remote or None,
    }
