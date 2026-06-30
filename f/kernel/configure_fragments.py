# SPDX-License-Identifier: copyleft-next-0.3.1
"""Configure method `fragments`: merge curated config fragments in canonical order.

You pick *which* fragments from the curated `linux-config-fragments` library; this
step imposes a *canonical* merge order so the result is deterministic regardless of
selection order: core structural fragments first, then by category, with builtin
(`=y`) overrides last (last-wins promotes the feature to built-in). Merging uses the
kernel tree's own `scripts/kconfig/merge_config.sh`.

The build identity is always baked into kernelrelease (see f/kernel/identity).

Equivalent bash, run inside the nixos-flake build devShell (cwd = worktree):

    ./scripts/kconfig/merge_config.sh $nflag -O "$build_dir" $ordered_fragment_paths
    # then bake the build identity into kernelrelease (see f/kernel/identity)

`nflag` is `-n` when `allnoconfig_base` (default true): unset symbols default to n.
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import DevShell, flags_to_env, vendor_dir
from f.kernel.identity import bake_identity

# Canonical category order. Builtin (=y) overrides always sort last (handled by a
# separate flag in the sort key) so last-wins promotes the matching feature to
# built-in. Unknown categories fall to rank 50.
_CATEGORY_RANK = {
    "core": 0,
    "arch": 1,
    "mem": 2,
    "security": 3,
    "storage": 4,
    "fs": 5,
    "net": 6,
    "virt": 7,
    "debug": 8,
    "test": 9,
    "rust": 10,
    "perf": 11,
}
# Within core, structural fragments lead and localversion trails.
_CORE_SUB_RANK = {
    "64bit.config": 0,
    "modules.config": 1,
    "core.config": 2,
    "systemd.config": 3,
    "initrd.config": 4,
    "localversion.config": 80,
    "localversion-noauto.config": 81,
}


def main(
    worktree: str,
    build_dir: str,
    fragments: list[str] | None = None,
    allnoconfig_base: bool = True,
    make_flags: str = "",
    label: str = "",
) -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    build = Path(build_dir)
    configs = vendor_dir(workers) / "linux-config-fragments/kernel/configs"
    if not configs.is_dir():
        raise FileNotFoundError(
            f"fragment library missing at {configs}; run f/workbench/init first"
        )

    if not fragments:
        raise ValueError("select at least one fragment")

    ordered = sorted(fragments, key=_sort_key)

    # Resolve each canonical-ordered entry to an absolute path under the library.
    resolved: list[Path] = []
    for frag in ordered:
        path = configs / frag
        if not path.is_file():
            raise FileNotFoundError(f"fragment not found: {path}")
        resolved.append(path)

    build.mkdir(parents=True, exist_ok=True)

    print("merging in order:", flush=True)
    for path in resolved:
        print(f"  {path.relative_to(configs)}", flush=True)

    shell = DevShell(workers)
    merge = str(Path(worktree) / "scripts/kconfig/merge_config.sh")
    merge_args = (["-n"] if allnoconfig_base else []) + ["-O", str(build)]
    # merge_config.sh invokes make relative to cwd, so run it from the worktree.
    # It takes no command-line make vars, so toolchain flags (LLVM=1) go via env.
    shell.run(
        merge,
        *merge_args,
        *[str(p) for p in resolved],
        cwd=worktree,
        env=flags_to_env(make_flags),
    )
    kernelrelease = bake_identity(shell, worktree, str(build), make_flags, label=label)

    print(
        f"configured {len(fragments)} fragment(s) -> {kernelrelease or 'unknown'}",
        flush=True,
    )

    return {
        "kernelrelease": kernelrelease or "unknown",
        "config": str(build / ".config"),
        "method": "fragments",
        "fragments": fragments,
    }


def _sort_key(frag: str) -> tuple[int, int, int, str]:
    """Canonical merge order key: (builtin-last, category, core-sub, name)."""
    if frag.startswith("builtin/"):
        builtin, rel = 1, frag[len("builtin/") :]
    else:
        builtin, rel = 0, frag
    category = rel.split("/", 1)[0]
    name = rel.rsplit("/", 1)[-1]
    cat_rank = _CATEGORY_RANK.get(category, 50)
    sub_rank = _CORE_SUB_RANK.get(name, 50) if category == "core" else 50
    return (builtin, cat_rank, sub_rank, name)
