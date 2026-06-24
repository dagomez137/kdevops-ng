# SPDX-License-Identifier: copyleft-next-0.3.1
"""Render an xfstests run's host-side config onto the `fstests` virtiofs share.

Writes onto `/var/lib/xfstests` (the share mount) the files the guest's
`xfstests@<section>.service` reads:

  - one `<section>.config` per selected section: a one-section xfstests
    `HOST_OPTIONS` config. `f/fstests/prepare` activates the running section's
    file as `local.config`; a one-section config is required because
    `./check -s <section>` on a multi-section file sets up TEST_DEV with the
    wrong FSTYP. There is ONE editable `local.config` (the `local_config` field;
    default = the shipped XFS starter catalog), device-agnostic: `FSTYP=xfs` plus
    each section's `MKFS_OPTIONS`/`MOUNT_OPTIONS`, no `TEST_DEV`/`SCRATCH_DEV`.
    The section list is auto-discovered from that config; `sections` narrows the
    run to a subset (empty = all). `render_config` injects the discovered
    `devices` into each selected section. A section whose filesystem block size
    is smaller than the device's logical sector size is skipped (mkfs.xfs would
    refuse it), not run.
  - `check.env`: the systemd `EnvironmentFile`: `HOST_OPTIONS=/var/lib/xfstests/
    local.config` (the GUEST path, since `./check` runs in the guest) and
    `XFSTESTS_CHECK_ARGS=<./check flags>` composed from the `check` inputs. The
    `xfstests-check` wrapper forces `RESULT_BASE=$PWD/results` with the unit's
    `WorkingDirectory=.../%v`, so results are keyed by the guest's kernel release at
    `<share>/<kver>/results/<section>`, read back by `f/fstests/collect`.

Files are written atomically and echoed to the job log. Returns the `[section]` names
that drive the flow's per-section forloop (the run set), plus any skipped sections. The
guest's `./check` creates its own `RESULT_BASE`; this step only cleans/rotates a prior
run's results under that kernel. The host never contacts the guest.

Equivalent commands:

    cat  > "$WORKERS_DIR/shared/fstests/<vm>/<section>.config"   # one per selected section
    cat  > "$WORKERS_DIR/shared/fstests/<vm>/check.env"          # XFSTESTS_CHECK_ARGS, HOST_OPTIONS
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from f.fstests.common import (
    GUEST_STATE_DIR,
    _atomic_write,
    build_check_args,
    device_sector,
    GUEST_PAGE_SIZE,
    inject_device_base,
    list_groups as _list_groups,
    list_vms as _list_vms,
    parse_sections,
    render_check_env,
    section_block,
    section_block_block_size,
    section_external,
    section_is_v4,
    section_results_dir,
    section_sector_size,
    share_dir,
    xfs_catalog_text,
)


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint for `vm_name`: see `f.fstests.common.list_vms`."""
    return _list_vms(filterText)


def list_groups(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_groups` entrypoint for `groups`: see `f.fstests.common.list_groups`."""
    return _list_groups(vm_name, filterText)


_FAILURE_RE = re.compile(r"^(?:out\.bad|dmesg|hints|core\..+)$")
_ROTATED_RE = re.compile(
    r"^\d+\.(?:out\.bad|dmesg|hints|full|core\..+|fsxgood|fsxlog)$"
)


def _emit(path, text: str) -> None:
    """Write a generated file atomically and echo it to the job log for auditability."""
    _atomic_write(path, text)
    print(f"+ wrote {path}", flush=True)
    print(text, flush=True)


def _rotate_results(section_dir) -> int | None:
    """Keep a prior run's data before this run overwrites it. A new run truncates the
    section's run-global `result.xml`/`check.log`; rename the existing ones to the next
    free zero-padded numeric suffix (`result.0001.xml`, `check.0001.log`) so earlier runs
    on this guest survive (the per-test failure artifacts are rotated by
    `_rotate_failure_artifacts` with the same index; `check.time` is left in place).
    Returns the index kept, else None.
    """
    if not (section_dir / "result.xml").is_file():
        return None
    n = 1
    while (section_dir / f"result.{n:04d}.xml").exists():
        n += 1
    for name in ("result.xml", "check.log"):
        f = section_dir / name
        if f.is_file():
            stem, _, ext = name.rpartition(".")
            f.rename(section_dir / f"{stem}.{n:04d}.{ext}")
    return n


def _rotate_failure_artifacts(section_dir: Path, n: int) -> int:
    """Preserve per-test failure forensics from prior runs alongside `result.<n>.xml`.
    A test `<base>` is failing if any of `<base>.{out.bad,dmesg,hints}` exists; rotate
    every `<base>.*` (including `.full`, `.fsxgood`, `.fsxlog`) in that group dir.
    `<base>.core.<hash>` rotates unconditionally (irreplaceable). `.notrun` and
    already-rotated `<base>.<digits>.*` files are left in place.
    """
    if not section_dir.is_dir():
        return 0
    count = 0
    for group_dir in sorted(p for p in section_dir.iterdir() if p.is_dir()):
        bases: dict[str, list[tuple[Path, str]]] = {}
        failing: set[str] = set()
        for entry in group_dir.iterdir():
            if not entry.is_file():
                continue
            base, dot, rest = entry.name.partition(".")
            if not dot or _ROTATED_RE.match(rest):
                continue
            bases.setdefault(base, []).append((entry, rest))
            if _FAILURE_RE.match(rest):
                failing.add(base)
        for base, items in bases.items():
            rotate = base in failing
            for entry, rest in items:
                if not rotate and not rest.startswith("core."):
                    continue
                target = group_dir / f"{base}.{n:04d}.{rest}"
                entry.rename(target)
                count += 1
    return count


def main(
    vm_name: str,
    kernel_version: str,
    local_config: str = "",
    sections: list[str] | None = None,
    clean_results: bool = False,
    logwrites: bool = True,
    devices: list[dict] | None = None,
    test_selection: str = "groups",
    groups: list[str] | None = None,
    exclude_group: str = "",
    exclude: str = "",
    report: str = "xunit",
    randomize: bool = False,
    tests: str = "",
    iterations: int = 1,
    loop_on_fail: int = 0,
    stop_on_fail: bool = True,
    test_timeout: int = 0,
    test_timeouts: dict[str, int] | None = None,
) -> dict:
    share = share_dir(vm_name)

    host_options = f"{GUEST_STATE_DIR}/local.config"
    check_args = build_check_args(test_selection, groups, exclude_group, exclude, report,
                                  randomize, tests, iterations, loop_on_fail, stop_on_fail)

    env_path = share / "check.env"
    _emit(env_path, render_check_env(host_options, check_args, test_timeout, test_timeouts))

    # The editable `local_config` textarea is the source; empty falls back to the
    # shipped starter catalog. The Sections dropdown lists exactly this config's
    # [section]s, so a selection can never name a section the config doesn't declare.
    cfg = (local_config or "").strip() or xfs_catalog_text()
    available = parse_sections(cfg)
    if not available:
        raise ValueError("local.config declares no [section]s to run")

    # A `sections` selection narrows to that subset (dropping any name not in the
    # config); empty/None runs every section the config declares.
    selected = [s for s in (sections or available) if s in available]
    if not selected:
        raise ValueError(
            f"none of the requested sections {sections} match a [section] in local.config; "
            f"pick from the Sections dropdown (it lists what local.config declares)"
        )

    if not devices or len(devices) < 2:
        raise ValueError(
            f"need >= 2 devices for TEST_DEV + SCRATCH_DEV, got {len(devices or [])}"
        )

    # XFS requires BOTH the block size and the (explicit) sector size to be >= the
    # device's logical sector size; skip any section below what mkfs.xfs would
    # enforce, rather than let it fail mid-run with `block size N cannot be smaller
    # than sector size M` (or the sector-size equivalent). A section with no explicit
    # `-s` has no sector floor of its own; gate it on block size alone.
    sector = device_sector(devices)
    skipped: list[dict] = []
    run_sections: list[str] = []
    section_text: dict[str, str] = {}
    for section in selected:
        block = section_block(cfg, section)
        bsize = section_block_block_size(block, section)
        ssize = section_sector_size(block, section)
        below: list[str] = []
        if bsize < sector:
            below.append(f"block {bsize}")
        if ssize is not None and ssize < sector:
            below.append(f"sector {ssize}")
        if below:
            reason = (
                f"{' and '.join(below)} < device sector {sector} "
                f"(needs a >= block/sector-size device)"
            )
            print(f"+ skipped {section}: {reason}", flush=True)
            skipped.append({"name": section, "reason": reason})
            continue
        # V4 (crc=0) XFS is unmountable once its block size exceeds the page size: the
        # kernel rejects it at mount ("Only pagesize or less is supported"), though
        # mkfs.xfs creates it. Skip here rather than let the section's xfstests unit
        # die on the mount mid-run.
        if section_is_v4(block, section) and bsize > GUEST_PAGE_SIZE:
            reason = (
                f"V4 (crc=0) block {bsize} > page size {GUEST_PAGE_SIZE}; the kernel "
                f"cannot mount a V4 filesystem with block size above the page size"
            )
            print(f"+ skipped {section}: {reason}", flush=True)
            skipped.append({"name": section, "reason": reason})
            continue
        # An external-device section (logdev/rtdev) consumes a third device beyond
        # TEST_DEV + SCRATCH_DEV; skip it (not fail mid-run) when too few are present.
        external = section_external(block)
        if external and len(devices) < 3:
            reason = f"needs 3 devices for an external {external}, have {len(devices)}"
            print(f"+ skipped {section}: {reason}", flush=True)
            skipped.append({"name": section, "reason": reason})
            continue
        # LOGWRITES_DEV (opt-in) carves one device beyond TEST + SCRATCH (+ external)
        # for the dm-log-writes replay log; skip the section, don't fail mid-run,
        # when the guest is a drive short.
        if logwrites:
            need = (3 if external else 2) + 1
            if len(devices) < need:
                reason = (
                    f"needs {need} devices for "
                    f"{'external ' + external + ' + ' if external else ''}LOGWRITES_DEV, "
                    f"have {len(devices)}"
                )
                print(f"+ skipped {section}: {reason}", flush=True)
                skipped.append({"name": section, "reason": reason})
                continue
        run_sections.append(section)
        section_text[section] = inject_device_base(block, devices, logwrites=logwrites)
    if not run_sections:
        raise ValueError(
            f"all selected sections skipped: device sector {sector} exceeds the "
            f"block/sector size of {', '.join(s['name'] for s in skipped)}; bring the "
            f"guest up with `boot_nvme.logical_block_size=512` for a 512-byte-sector "
            f"device that runs the sub-4K block and sector sizes"
        )

    # One single-section config per section; prepare activates the running one as
    # local.config (the unit's HOST_OPTIONS).
    section_configs = []
    rotated: dict[str, int] = {}
    archived_failures: dict[str, int] = {}
    cleaned: list[str] = []
    for section in run_sections:
        # Prior results live on the VM's share, keyed by the guest's kernel
        # release: <share>/<kver>/results/<section>. The guest recreates this at
        # run time (unit WorkingDirectory=.../%v + ./check's mkdir), so
        # clean/rotate here only touch a prior run's data.
        section_dir = section_results_dir(vm_name, kernel_version, section)
        if clean_results and section_dir.is_dir():
            # Opposite of rotate-preserve: a fresh start, dropping prior .out.bad /
            # result.xml / cores / .NNNN rotations. The guest's ./check (RESULT_BASE)
            # recreates section_dir at run time, so don't pre-create it here.
            shutil.rmtree(section_dir, ignore_errors=True)
            print(f"+ cleaned {section_dir} (clean_results)", flush=True)
            cleaned.append(section)
        else:
            kept = _rotate_results(section_dir)
            if kept is not None:
                rotated[section] = kept
                print(f"+ kept prior {section} results as result.{kept:04d}.xml / check.{kept:04d}.log", flush=True)
                archived = _rotate_failure_artifacts(section_dir, kept)
                if archived > 0:
                    archived_failures[section] = archived
                    print(f"+ kept {archived} prior {section} failure artifacts as <base>.{kept:04d}.<suffix>", flush=True)
        # Lay down the one-section config (whichever branch ran above).
        path = share / f"{section}.config"
        _emit(path, section_text[section])
        section_configs.append(str(path))

    print(f"sections: {run_sections}", flush=True)
    return {
        "vm_name": vm_name,
        "kernel_version": kernel_version,
        "share_dir": str(share),
        "results_dir": str(section_results_dir(vm_name, kernel_version, run_sections[0]).parent),
        "sections": run_sections,
        "skipped": skipped,
        "rotated": rotated,
        "archived_failures": archived_failures,
        "cleaned": cleaned,
        "section_configs": section_configs,
        "host_options": host_options,
        "check_env_path": str(env_path),
        "check_args": check_args,
    }
