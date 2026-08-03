# SPDX-License-Identifier: copyleft-next-0.3.1
"""Render a blktests run's host-side config onto the `blktests` virtiofs share.

Writes onto `/var/lib/blktests` (the share mount) the files the guest's
`blktests@<group>.service` reads:

  - `config`: the ONE rendered blktests config (a sourced bash file the unit
    passes to `./check` via `--config`). Every knob maps one-to-one to an
    upstream `config.example` variable under its upstream name; only what the
    user set is emitted (`NORMAL_USER` always), with `TEST_DEVS`/`EXCLUDE` as
    bash arrays. The gated raw `config` override (`edit_config`) replaces the
    rendered file wholesale.
  - `<group>.env`: the group's own systemd `EnvironmentFile` (read as `%i.env`),
    so `systemctl start blktests@<group>` is self-contained:
    `BLKTESTS_ARGS=<positionals>` carries only the positional args (the group
    name, or that group's explicit `group/nnn` test list). The unit itself
    supplies `--config` and `--output=.../%v/results`, so results are keyed by
    the guest's kernel release at `<share>/<kver>/results`, read back by
    `f/blktests/collect`.

Files are written atomically and echoed to the job log. Returns the ordered
group names that drive the flow's per-group forloop. A stale `<group>.env`
whose group left the selection is pruned; `clean_results` removes the whole
`<kver>/results` tree for a fresh start (the guest's `./check` recreates it at
run time). The host never contacts the guest.

Equivalent commands:

    cat > "$WORKERS_DIR/shared/blktests/<vm>/config"
    cat > "$WORKERS_DIR/shared/blktests/<vm>/<group>.env"   # one per selected group
"""

from __future__ import annotations

import shutil
from pathlib import Path

from f.blktests.common import (
    _atomic_write,
    build_args,
    render_blktests_config,
    results_dir,
    share_dir,
)
from f.blktests.common import (
    list_groups as _list_groups,
)
from f.blktests.common import (
    list_vms as _list_vms,
)


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.common.remote.list_vms`."""
    return _list_vms(filterText)


def list_groups(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_groups` entrypoint for `groups`: see
    `f.blktests.common.list_groups`."""
    return _list_groups(vm_name, filterText)


def _emit(path: Path, text: str) -> None:
    """Write a generated file atomically and echo it to the job log for auditability."""
    _atomic_write(path, text)
    print(f"+ wrote {path}", flush=True)
    print(text, flush=True)


def main(
    vm_name: str,
    kernel_version: str,
    test_selection: str = "groups",
    groups: list[str] | None = None,
    tests: str | list | None = "",
    test_devs: list[str] | None = None,
    exclude: list[str] | None = None,
    device_only: bool = False,
    quick_run: bool = False,
    timeout: int = 0,
    run_zoned_tests: bool = False,
    nvmet_trtypes: list[str] | None = None,
    nvmet_blkdev_types: list[str] | None = None,
    nvme_img_size: str = "1G",
    nvme_num_iter: int = 1000,
    use_rxe: bool = False,
    throtl_blkdev_types: list[str] | None = None,
    edit_config: bool = False,
    config: str = "",
    clean_results: bool = False,
    test_timeout: int = 0,
    test_timeouts: dict[str, int] | None = None,
) -> dict:
    share = share_dir(vm_name)

    # The per-group positionals also fix the run set: one BLKTESTS_ARGS entry
    # per blktests@<group> instance, groups-vs-tests exclusivity enforced.
    args = build_args(test_selection, groups, tests)
    run_groups = list(args)

    config_path = share / "config"
    _emit(
        config_path,
        render_blktests_config(
            test_devs=test_devs,
            device_only=device_only,
            quick_run=quick_run,
            timeout=timeout,
            exclude=exclude,
            run_zoned_tests=run_zoned_tests,
            nvmet_trtypes=nvmet_trtypes,
            nvmet_blkdev_types=nvmet_blkdev_types,
            nvme_img_size=nvme_img_size,
            nvme_num_iter=nvme_num_iter,
            use_rxe=use_rxe,
            throtl_blkdev_types=throtl_blkdev_types,
            test_timeout=test_timeout,
            test_timeouts=test_timeouts,
            edit_config=edit_config,
            config=config,
        ),
    )

    results = results_dir(vm_name, kernel_version)
    cleaned = False
    if clean_results and results.is_dir():
        shutil.rmtree(results, ignore_errors=True)
        print(f"+ cleaned {results} (clean_results)", flush=True)
        cleaned = True

    group_envs: list[str] = []
    for group, positionals in args.items():
        env_path = share / f"{group}.env"
        _emit(env_path, f"BLKTESTS_ARGS={positionals}\n")
        group_envs.append(str(env_path))

    # Prune a stale <group>.env whose group left the selection, so the share
    # mirrors this run's set and a hand-started stale instance runs nothing old.
    keep = set(run_groups)
    for path in sorted(share.glob("*.env")):
        if path.is_file() and path.stem not in keep:
            path.unlink()
            print(f"+ removed {path}", flush=True)

    print(f"run {run_groups}", flush=True)
    return {
        "vm_name": vm_name,
        "kernel_version": kernel_version,
        "share_dir": str(share),
        "results_dir": str(results),
        "groups": run_groups,
        "group_envs": group_envs,
        "config_path": str(config_path),
        "cleaned": cleaned,
    }
