# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fetch the QEMU devel layer onto a developer worktree and relocate its clangd index.

The consumer-side companion to `f/qemu/publish_devel`, and the QEMU analog of
`f/kernel/fetch_devel`. Resolve the `qemu-devel-<version>-<label>-<identity>` store
path, materialize the build dir's developer subset (meson's index plus the generated
headers and sources) into this worktree's build dir, and re-point everything the layer
recorded at the builder's paths.

Same-host leaves `remote`/`remote_index` empty and resolves the layer from the local
index. Cross-host sets `remote` to an ssh host and `remote_index` to that builder's
`store-index` directory, and `store.resolve` reads the peer's index entry over ssh to
learn the store path, pulls it with `nix copy`, and indexes it locally. `build_dir`
defaults to the worktree's own `build` child and must stay under it.

`synced` says whether the worktree carries the commit this build produced. The build
flow's tail passes through what `f/workbench/worktree/init` reports, and that step leaves
a tree it cannot move without discarding the developer's work exactly where it is. When
it is false the index names source files at paths this tree may not carry at all, so
nothing is materialized and `fetched` comes back false. It defaults on, so a standalone
call is unaffected.

The kernel step regenerates its index; this one relocates. kbuild leaves a `.cmd`
command database, so `f/kernel/fetch_devel` replays it with the kernel's own
`gen_compile_commands.py` against the local tree and the paths come out right by
construction. Meson leaves no such database: it writes the finished
`compile_commands.json` when it configures, recording the builder's absolute paths in
`directory`, in `file`, and repeatedly inside each `command` (`-isystem
<worktree>/linux-headers`, `-iquote <worktree>/include`,
`-ffile-prefix-map=<worktree>=/qemu`). So the index ships as-is and is rewritten here,
substituting the builder's build dir and worktree for this host's. The substitution is
textual because the builder's path appears in three keys and many times within one of
them; a per-key rewrite would miss the embedded copies, and `/` needs no JSON escaping.

The layer's symlinks are relocated the same way. `build/linux-headers/asm` is the
load-bearing one: it is how the relative `-isystem linux-headers` include reaches the
target's headers, and meson writes it as an absolute path into the builder's worktree,
so it dangles on every host but that one until it is re-pointed.

Equivalent bash, run inside the nixos-flake transfer devShell for the cross-host half:

    sp=$(ssh "$remote" readlink "$remote_index"/qemu-devel-"$(basename "$prefix")")
    nix copy --from ssh://"$remote" "$sp" --no-check-sigs
    nix build "$sp" --out-link "$index"/qemu-devel-"$(basename "$prefix")"
    cp --recursive --force "$sp"/. "$worktree/build"/
    chmod --recursive u+w "$worktree/build"
    # then rewrite the builder's paths out of the index and the symlinks
    sed --in-place "s|$old_build|$worktree/build|g; s|$old_root|$worktree|g" \\
        "$worktree/build/compile_commands.json"
    cp "$worktree/build/compile_commands.json" "$worktree/compile_commands.json"
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from f.common import store
from f.common.devshell import run_logged

_INDEXES = ("compile_commands.json", "rust-project.json")


def main(
    worktree: str,
    prefix: str,
    remote: str = "",
    remote_index: str = "",
    build_dir: str = "",
    required: bool = False,
    synced: bool = True,
) -> dict:
    wt = Path(worktree)
    if not (wt / "VERSION").is_file():
        raise FileNotFoundError(f"no QEMU source checkout at {wt}")
    build = Path(build_dir) if build_dir else wt / "build"
    if wt.resolve() not in build.resolve().parents:
        raise ValueError(
            f"build_dir {build} must live under the worktree {wt}: the index's relative "
            "include paths resolve against the build dir, so only a child resolves them"
        )
    build.mkdir(parents=True, exist_ok=True)

    if not synced:
        print(
            f"worktree {wt} was left at its own commit, not this build's; not "
            "relocating the index, it names source files this tree may not carry",
            flush=True,
        )
        return {
            "fetched": False,
            "worktree": str(wt),
            "build_dir": str(build),
            "prefix": prefix,
        }

    workers = Path(os.environ["WORKERS_DIR"])
    identity = Path(prefix).name
    name = f"qemu-devel-{identity}"

    sp = store.resolve(name, workers, remote, remote_index)
    if sp is None:
        if required:
            raise FileNotFoundError(
                f"devel layer {name} not found locally or on the peer; a worktree "
                "asked to be indexed cannot be, so this is a failure rather than a "
                "bare checkout. Build this identity with reuse off to publish it."
            )
        print(f"devel layer {identity}: not found locally or on the peer", flush=True)
        return {
            "fetched": False,
            "worktree": str(wt),
            "build_dir": str(build),
            "prefix": prefix,
        }

    run_logged(
        ["cp", "--recursive", "--force", f"{sp.rstrip('/')}/.", str(build) + "/"]
    )
    run_logged(["chmod", "--recursive", "u+w", str(build)])
    print(f"materialized devel layer {sp} -> {build}", flush=True)

    moved = relocate(build, wt)

    return {
        "fetched": True,
        "worktree": str(wt),
        "build_dir": str(build),
        "prefix": prefix,
        "store_path": sp,
        "remote": remote or None,
        **moved,
    }


def relocate(build: Path, worktree: Path) -> dict:
    """Re-point a materialized devel layer from the builder's paths at this worktree.

    The builder's build dir is read back from the index's own `directory` key, and its
    worktree is that path's parent (ADR-0003 keeps the build dir a child of the
    source). Both are substituted, longest first, so a build dir the builder named
    something other than `build` still resolves. Returns the source-root index path,
    its entry count, and how many symlinks were re-pointed.
    """
    index = build / "compile_commands.json"
    if not index.is_file():
        print(f"no compile_commands.json in {build}; headers only", flush=True)
        return {"compile_commands": None, "entries": 0, "relinked": 0}

    text = index.read_text()
    entries = json.loads(text)
    old_build = entries[0].get("directory", "") if entries else ""
    if not old_build:
        raise ValueError(f"{index} names no build directory to relocate from")
    old_root = os.path.dirname(old_build)
    subs = ((old_build, str(build)), (old_root, str(worktree)))

    for name in _INDEXES:
        path = build / name
        if path.is_file():
            _write(path, _substitute(path.read_text(), subs))

    text = _substitute(text, subs)
    entries = json.loads(text)

    relinked = 0
    for dirpath, dirnames, filenames in os.walk(build):
        for leaf in dirnames + filenames:
            link = Path(dirpath) / leaf
            if not link.is_symlink():
                continue
            target = os.readlink(link)
            moved = _substitute(target, subs)
            if moved == target:
                continue
            link.unlink()
            link.symlink_to(moved)
            relinked += 1
    print(f"re-pointed {relinked} symlink(s) from {old_root}", flush=True)

    root_index = worktree / "compile_commands.json"
    _write(root_index, text)
    print(f"wrote {root_index} ({len(entries)} entries)", flush=True)
    return {
        "compile_commands": str(root_index),
        "entries": len(entries),
        "relinked": relinked,
    }


def _substitute(text: str, subs: tuple[tuple[str, str], ...]) -> str:
    for old, new in subs:
        text = text.replace(old, new)
    return text


def _write(path: Path, text: str) -> None:
    """Write through a sibling temp file so a running clangd never reads a half file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
