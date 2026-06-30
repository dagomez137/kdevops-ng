# SPDX-License-Identifier: copyleft-next-0.3.1
"""Inspect and prune the build Store's identity catalog.

Runnable step. The build Store indexes every published identity as a symlink
under `SYSTEM_DIR/store-index/<name>` -> its `/nix/store` path, and each
symlink is also an indirect Nix GC root (so the path survives
`nix store gc`). Names are `kernel-<release>`, `kernel-devel-<release>`,
or `qemu-<identity>`. This step reads and maintains that catalog through four
actions:

- `list` (default) enumerates the local catalog, sizing each entry by its
  closure, and prints a count/total summary; with `remote` and `remote_index`
  set it also reports the peer's entry names over one cheap ssh.
- `inspect` resolves the selected `names` to their store paths and closure
  sizes, and the peer's store path for each when a remote is given.
- `forget` removes the selected local index entries (their GC roots), guarded by
  `confirm`, so the store paths become reclaimable on the next collection.
- `prune` removes every local entry whose store path is already gone (dangling),
  needing no confirmation.

Equivalent command:

    ls -l "$STORE_INDEX_DIR/"
    nix path-info --closure-size --human-readable \\
        "$(readlink "$STORE_INDEX_DIR/<name>")"
    rm "$STORE_INDEX_DIR/<name>" && nix store gc
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
        entries.append(
            {
                "name": entry.name,
                "kind": _kind(entry.name),
                "store_path": target,
                "valid": Path(target).exists(),
            }
        )

    sizes = _closure_sizes([e["store_path"] for e in entries if e["valid"]])
    for e in entries:
        e["size_bytes"] = sizes.get(e["store_path"]) if e["valid"] else None
    entries.sort(key=lambda e: e["name"])

    total = sum(e["size_bytes"] or 0 for e in entries)
    print(
        f"store-index: {len(entries)} entries, {total / _MIB:.1f} MiB total", flush=True
    )
    for e in entries:
        mib = (e["size_bytes"] or 0) / _MIB
        state = "ok" if e["valid"] else "DANGLING"
        print(f"  {e['name']}  {mib:.1f} MiB  {state}", flush=True)

    peer = None
    if remote and remote_index:
        names = (
            DevShell(workers, "transfer")
            .capture("ssh", remote, "ls", "-1", remote_index, check=False)
            .split()
        )
        print(f"peer {remote}: {len(names)} entries", flush=True)
        peer = {"remote": remote, "names": names}

    return {"action": "list", "local": entries, "total_bytes": total, "peer": peer}


def _inspect(workers: Path, names: list[str], remote: str, remote_index: str) -> dict:
    if not names:
        raise ValueError("inspect requires at least one name")
    entries = []
    for name in names:
        sp = store.local_path(name)
        size_bytes = _closure_sizes([sp]).get(sp) if sp else None
        peer_store_path = None
        if remote and remote_index:
            peer_store_path = store.peer_path(workers, remote, remote_index, name)
        print(
            f"inspect {name}: store_path={sp} size_bytes={size_bytes} "
            f"peer={peer_store_path}",
            flush=True,
        )
        entries.append(
            {
                "name": name,
                "store_path": sp,
                "valid": sp is not None,
                "size_bytes": size_bytes,
                "peer_store_path": peer_store_path,
            }
        )
    return {"action": "inspect", "entries": entries}


def _forget(names: list[str], confirm: bool) -> dict:
    if not names:
        raise ValueError("forget requires at least one name")
    if not confirm:
        return {
            "action": "forget",
            "removed": [],
            "reason": "set confirm=true to remove the GC root(s)",
        }
    removed = []
    for name in names:
        entry = store.index_dir() / name
        store_path = os.path.realpath(entry) if entry.is_symlink() else None
        entry.unlink(missing_ok=True)
        print(
            f"forgot {name} (store path reclaimable on next nix store gc)",
            flush=True,
        )
        removed.append({"name": name, "store_path": store_path})
    print(f"forgot {len(removed)} entries", flush=True)
    return {"action": "forget", "removed": removed, "count": len(removed)}


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


def list_catalog(filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_catalog` entrypoint for `names`: live catalog entries."""
    names = store.list_index("")
    if filterText:
        names = [n for n in names if filterText.lower() in n.lower()]
    return [{"label": n, "value": n} for n in names]


def main(
    action: str = "list",
    names: list[str] | None = None,
    name: str = "",
    remote: str = "",
    remote_index: str = "",
    confirm: bool = False,
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    selected: list[str] = []
    for entry in ([name] if name else []) + list(names or []):
        if entry and entry not in selected:
            selected.append(entry)
    if action == "list":
        return _list(workers, remote, remote_index)
    if action == "inspect":
        return _inspect(workers, selected, remote, remote_index)
    if action == "forget":
        return _forget(selected, confirm)
    if action == "prune":
        return _prune()
    raise ValueError(f"unknown action {action!r} (list|inspect|forget|prune)")
