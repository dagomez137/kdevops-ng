# SPDX-License-Identifier: copyleft-next-0.3.1
"""Inspect and prune the build Store's identity catalog.

Runnable step. The build Store indexes every published identity as a symlink
under `WORKERS_DIR/shared/store-index/<name>` -> its `/nix/store` path, and each
symlink is also an indirect Nix GC root (so the path survives
`nix-collect-garbage`). Names are `kernel-<release>`, `kernel-devel-<release>`,
or `qemu-<identity>`. This step reads and maintains that catalog through four
actions:

- `list` (default) enumerates the local catalog, sizing each entry by its
  closure, and prints a count/total summary; with `remote` and `remote_index`
  set it also reports the peer's entry names over one cheap ssh.
- `inspect` resolves a single `name` to its store path and closure size, and the
  peer's store path for it when a remote is given.
- `forget` removes one local index entry (its GC root), guarded by `confirm`, so
  the store path becomes reclaimable on the next collection.
- `prune` removes every local entry whose store path is already gone (dangling),
  needing no confirmation.

Equivalent command:

    ls -l "$WORKERS_DIR/shared/store-index/"
    nix path-info --closure-size --human-readable \\
        "$(readlink "$WORKERS_DIR/shared/store-index/<name>")"
    rm "$WORKERS_DIR/shared/store-index/<name>" && nix-collect-garbage
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common import store
from f.common.devshell import DevShell, Nix

_MIB = 1024 * 1024


def _kind(name: str) -> str:
    if name.startswith("kernel-devel-"):
        return "kernel-devel"
    if name.startswith("kernel-"):
        return "kernel"
    if name.startswith("qemu-"):
        return "qemu"
    return "other"


def _closure_sizes(store_paths: list[str]) -> dict[str, int]:
    """Map each store path to its closure size in one batched `nix path-info`."""
    if not store_paths:
        return {}
    out = Nix().capture("path-info", "--closure-size", *store_paths)
    sizes: dict[str, int] = {}
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            sizes[fields[0]] = int(fields[-1])
    return sizes


def _list(workers: Path, remote: str, remote_index: str) -> dict:
    entries = []
    for entry in store.index_dir().iterdir():
        if not entry.is_symlink():
            continue
        target = os.path.realpath(entry)
        entries.append({
            "name": entry.name,
            "kind": _kind(entry.name),
            "store_path": target,
            "valid": Path(target).exists(),
        })

    sizes = _closure_sizes([e["store_path"] for e in entries if e["valid"]])
    for e in entries:
        e["size_bytes"] = sizes.get(e["store_path"]) if e["valid"] else None
    entries.sort(key=lambda e: e["name"])

    total = sum(e["size_bytes"] or 0 for e in entries)
    print(f"store-index: {len(entries)} entries, {total / _MIB:.1f} MiB total", flush=True)
    for e in entries:
        mib = (e["size_bytes"] or 0) / _MIB
        state = "ok" if e["valid"] else "DANGLING"
        print(f"  {e['name']}  {mib:.1f} MiB  {state}", flush=True)

    peer = None
    if remote and remote_index:
        names = DevShell(workers, "transfer").capture(
            "ssh", remote, "ls", "-1", remote_index, check=False).split()
        print(f"peer {remote}: {len(names)} entries", flush=True)
        peer = {"remote": remote, "names": names}

    return {"action": "list", "local": entries, "total_bytes": total, "peer": peer}


def _inspect(workers: Path, name: str, remote: str, remote_index: str) -> dict:
    if not name:
        raise ValueError("inspect requires a name")
    sp = store.local_path(name)
    size_bytes = None
    if sp:
        size_bytes = _closure_sizes([sp]).get(sp)
    peer_store_path = None
    if remote and remote_index:
        peer_store_path = store.peer_path(workers, remote, remote_index, name)
    print(f"inspect {name}: store_path={sp} size_bytes={size_bytes} "
          f"peer={peer_store_path}", flush=True)
    return {
        "action": "inspect",
        "name": name,
        "store_path": sp,
        "valid": sp is not None,
        "size_bytes": size_bytes,
        "peer_store_path": peer_store_path,
    }


def _forget(name: str, confirm: bool) -> dict:
    if not name:
        raise ValueError("forget requires a name")
    if not confirm:
        return {"action": "forget", "removed": False,
                "reason": "set confirm=true to remove the GC root"}
    entry = store.index_dir() / name
    store_path = os.path.realpath(entry) if entry.is_symlink() else None
    entry.unlink(missing_ok=True)
    print(f"forgot {name} (store path reclaimable on next nix-collect-garbage)", flush=True)
    return {"action": "forget", "name": name, "removed": True, "store_path": store_path}


def _prune() -> dict:
    removed = []
    for entry in store.index_dir().iterdir():
        if not entry.is_symlink():
            continue
        if Path(os.path.realpath(entry)).exists():
            continue
        entry.unlink()
        print(f"pruned {entry.name} (dangling)", flush=True)
        removed.append(entry.name)
    print(f"pruned {len(removed)} dangling entries", flush=True)
    return {"action": "prune", "removed": removed, "count": len(removed)}


def main(action: str = "list", name: str = "", remote: str = "",
         remote_index: str = "", confirm: bool = False) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    if action == "list":
        return _list(workers, remote, remote_index)
    if action == "inspect":
        return _inspect(workers, name, remote, remote_index)
    if action == "forget":
        return _forget(name, confirm)
    if action == "prune":
        return _prune()
    raise ValueError(f"unknown action {action!r} (list|inspect|forget|prune)")
