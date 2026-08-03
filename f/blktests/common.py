# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Shared library for the f/blktests/* steps (host side of a blktests run);
# imported as f.blktests.common, not a runnable step. Touches only the host end
# of the rw virtiofs share (tag `blktests`) the guest mounts at
# /var/lib/blktests.
#
# The contract with the guest side (kept verbatim on both ends):
#   * guest mount: /var/lib/blktests (GUEST_STATE_DIR), share tag `blktests`;
#   * <share>/config          = the rendered blktests config (a sourced bash
#                               file the unit passes to ./check via --config);
#   * <share>/<group>.env     = the systemd EnvironmentFile the unit reads for
#                               %i (BLKTESTS_ARGS=<positional args>: the group
#                               name, or an explicit space-separated test list
#                               such as "block/002 block/005");
#   * <share>/<kver>/results/ = the ./check --output tree, keyed by the guest's
#                               kernel release (the unit passes
#                               --output=.../%v/results): one TSV file per test
#                               at results/<devdir>/<group>/<nnn>, where
#                               <devdir> is `nodev` or a device basename,
#                               optionally suffixed by a set_conditions variant
#                               (nodev_tr_tcp_bd_file), so ONE test number can
#                               yield SEVERAL result rows;
#   * <share>/<kver>/report.json = the run rollup f/blktests/report writes.
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

# Re-exported so the steps import the transport and VM listing from one place,
# matching f/fstests/common; the transport itself lives in f.common.remote.
from f.common.remote import RemoteSystemd as RemoteSystemd
from f.common.remote import list_vms as list_vms

# Guest-side constants (the blktests@.service state dir + share mount).
GUEST_STATE_DIR = "/var/lib/blktests"
GUEST_TAG = "blktests"

# The curated group catalog: name, one-line description, test count, in catalog
# order. The installed groups discover enumerates from the guest's package tree
# are the ground truth; this supplies the labels, the fallback before the first
# discovery, and the default run set.
GROUPS: list[dict] = [
    {"name": "block", "description": "Generic block layer tests", "tests": 44},
    {"name": "nvme", "description": "NVMe device and fabrics tests", "tests": 61},
    {"name": "meta", "description": "Testing framework self-tests", "tests": 24},
    {"name": "srp", "description": "SRP over RDMA tests", "tests": 15},
    {"name": "zbd", "description": "Zoned block device tests", "tests": 14},
    {"name": "loop", "description": "Loop device tests", "tests": 13},
    {"name": "scsi", "description": "SCSI generic device tests", "tests": 10},
    {"name": "throtl", "description": "blk-throttle tests", "tests": 8},
    {"name": "ublk", "description": "ublk driver tests", "tests": 6},
    {"name": "nbd", "description": "NBD tests", "tests": 4},
    {"name": "md", "description": "md raid tests", "tests": 4},
    {"name": "dm", "description": "Device-mapper tests", "tests": 3},
    {"name": "rnbd", "description": "RNBD tests", "tests": 2},
    {"name": "blktrace", "description": "blktrace infrastructure tests", "tests": 2},
    {"name": "bcache", "description": "bcache tests", "tests": 1},
]


def group_names() -> list[str]:
    """The catalog group names, in catalog order (run-form fallback + validation)."""
    return [g["name"] for g in GROUPS]


def default_groups() -> list[str]:
    """The default run set: every catalog group except `meta`, upstream's own
    no-argument default (`check` with no positionals runs everything but the
    framework self-tests)."""
    return [g["name"] for g in GROUPS if g["name"] != "meta"]


def _workers() -> Path:
    return Path(os.environ["WORKERS_DIR"])


def share_dir(vm_name: str, workers: Path | None = None) -> Path:
    """Host path of the VM's `blktests` virtiofs share, name-escape hardened.

    `$WORKERS_DIR/shared/blktests/<vm_name>`. Lives under `shared/` so every
    worker sees the same bytes the guest's virtiofsd serves. `vm_name` is
    resolved and checked to sit directly under the share root, so a crafted
    name (`../x`) can never write outside it.
    """
    root = (workers or _workers()) / "shared/blktests"
    path = (root / vm_name).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"vm_name {vm_name!r} resolves outside {root}")
    return path


def _safe_kver(kernel_version: str) -> str:
    """Validate a `uname -r` string is a single path component (no `/`, no `..`)."""
    kv = (kernel_version or "").strip()
    if not kv or "/" in kv or kv in (".", ".."):
        raise ValueError(
            f"invalid kernel_version {kernel_version!r}; expected a `uname -r` value "
            f"(discover returns it as kernel_version)"
        )
    return kv


def _safe_group(group: str) -> str:
    """Validate a blktests group name is a single path component (no `/`, no `..`)."""
    g = (group or "").strip()
    if not g or "/" in g or g in (".", ".."):
        raise ValueError(f"invalid group {group!r}; expected a blktests group name")
    return g


def results_dir(vm_name: str, kernel_version: str, workers: Path | None = None) -> Path:
    """The run's `./check --output` tree on the VM's share, keyed by the kernel.

    `<share_dir>/<kver>/results`: the host view of the guest's
    `/var/lib/blktests/<kver>/results`. The unit's `--output=.../%v/results`
    keys by kernel release (so the same closure, booted into different kernels,
    never clobbers). Path-traversal hardened: a crafted kver can't escape the
    VM's share.
    """
    base = share_dir(vm_name, workers)
    path = (base / _safe_kver(kernel_version) / "results").resolve()
    if base not in path.parents:
        raise ValueError(f"kernel_version {kernel_version!r} resolves outside {base}")
    return path


def report_path(
    vm_name: str, kernel_version: str = "", workers: Path | None = None
) -> Path:
    """Where `f/blktests/report` writes the run rollup:
    `<share>/<kver>/report.json`, keyed by the guest's kernel release so two
    kernels' runs on one guest never clobber each other; the share root when
    the kernel is unknown (degraded run)."""
    share = share_dir(vm_name, workers)
    if not (kernel_version or "").strip():
        return share / "report.json"
    return share / _safe_kver(kernel_version) / "report.json"


_GROUP_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_TEST_NAME_RE = re.compile(r"^([a-zA-Z0-9_-]+)/([0-9]{3})$")


def build_args(
    test_selection: str = "groups",
    groups: list[str] | None = None,
    tests: str | list | None = "",
) -> dict[str, str]:
    """Compose the per-group `BLKTESTS_ARGS` positionals, one entry per
    `blktests@<group>.service` instance: `{"<group>": "<positionals>"}`.

    `./check` takes only positionals here (the group name, or explicit
    `group/nnn` test names); all tunables live in the rendered config.
    `test_selection` enforces a mutual exclusion the bare `./check` does NOT:
    `groups` runs each selected group whole (empty falls back to upstream's own
    no-argument default, every group except `meta`) and ignores `tests`;
    `tests` splits the space-separated `group/nnn` list per group (each test's
    group derived from its prefix) and ignores `groups`. Explicitly named
    tests bypass `EXCLUDE`/`DEVICE_ONLY`/`QUICK_RUN`, per upstream. A malformed
    group or test name raises rather than silently running nothing.
    """
    if test_selection == "tests":
        if isinstance(tests, str):
            names = (tests or "").split()
        else:
            names = [t for t in (tests or []) if t]
        if not names:
            raise ValueError("tests mode selected but no tests given")
        out: dict[str, list[str]] = {}
        for name in names:
            m = _TEST_NAME_RE.match(name)
            if not m:
                raise ValueError(
                    f"invalid test {name!r}; expected <group>/<nnn> (e.g. block/002)"
                )
            out.setdefault(m.group(1), []).append(name)
        return {group: " ".join(members) for group, members in out.items()}
    selected: list[str] = []
    for group in groups or default_groups():
        if not _GROUP_NAME_RE.match(group or ""):
            raise ValueError(f"invalid group {group!r}; expected a blktests group name")
        if group not in selected:
            selected.append(group)
    return {group: group for group in selected}


def _atomic_write(path: Path, data: str, mode: int = 0o644) -> None:
    """Write via a hidden temp file + rename so a concurrent reader on the shared
    dir (the guest's virtiofsd) never sees a half-written `config`/`.env`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        # fdopen owns the fd from here; fchmod inside the with block so the raw
        # fd can never leak on a chmod failure.
        with os.fdopen(fd, "w") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def render_blktests_config(
    test_devs: list[str] | None = None,
    device_only: bool = False,
    quick_run: bool = False,
    timeout: int = 0,
    exclude: list[str] | None = None,
    run_zoned_tests: bool = False,
    normal_user: str = "blktests",
    nvmet_trtypes: list[str] | None = None,
    nvmet_blkdev_types: list[str] | None = None,
    nvme_img_size: str = "",
    nvme_num_iter: int = 0,
    use_rxe: bool = False,
    throtl_blkdev_types: list[str] | None = None,
    test_timeout: int = 0,
    test_timeouts: dict[str, int] | None = None,
    edit_config: bool = False,
    config: str = "",
) -> str:
    """The blktests config text (a sourced bash file, passed via `--config`).

    Every knob maps one-to-one to an upstream `config.example` variable under
    its upstream name; only what the caller set (non-empty, non-zero) is
    emitted, so the file mirrors what the user chose and blktests' own defaults
    cover the rest. `TEST_DEVS` and `EXCLUDE` render as bash arrays
    (`TEST_DEVS=(/dev/nvme1n1 /dev/nvme2n1)`); the space-joined list variables
    (`NVMET_TRTYPES`, `NVMET_BLKDEV_TYPES`, `THROTL_BLKDEV_TYPES`,
    `TEST_TIMEOUTS`) are quoted. `NORMAL_USER` is always emitted (the closure
    ships the matching unprivileged account). `TEST_TIMEOUT`/`TEST_TIMEOUTS`
    are the per-test scope watchdog the packaged `check` reads (`RuntimeMaxSec`
    on each test's transient scope), the same knob names as fstests.

    When `edit_config` is set and `config` is non-empty, the raw text replaces
    the rendered config wholesale (the gated advanced override).
    """
    raw = (config or "").strip()
    if edit_config and raw:
        return raw + "\n"
    lines: list[str] = []
    devs = [d for d in (test_devs or []) if d]
    if devs:
        lines.append(f"TEST_DEVS=({' '.join(devs)})")
    if device_only:
        lines.append("DEVICE_ONLY=1")
    if quick_run:
        lines.append("QUICK_RUN=1")
    # QUICK_RUN without TIMEOUT is a fatal error in check; --quick defaults the
    # budget to 30 seconds, so mirror that here when no explicit value is set.
    if timeout or quick_run:
        lines.append(f"TIMEOUT={int(timeout) or 30}")
    excludes = [e for e in (exclude or []) if e]
    if excludes:
        lines.append(f"EXCLUDE=({' '.join(excludes)})")
    if run_zoned_tests:
        lines.append("RUN_ZONED_TESTS=1")
    lines.append(f"NORMAL_USER={normal_user or 'blktests'}")
    trtypes = " ".join(t for t in (nvmet_trtypes or []) if t)
    if trtypes:
        lines.append(f'NVMET_TRTYPES="{trtypes}"')
    blkdev_types = " ".join(t for t in (nvmet_blkdev_types or []) if t)
    if blkdev_types:
        lines.append(f'NVMET_BLKDEV_TYPES="{blkdev_types}"')
    if nvme_img_size:
        lines.append(f"NVME_IMG_SIZE={nvme_img_size}")
    if nvme_num_iter:
        lines.append(f"NVME_NUM_ITER={int(nvme_num_iter)}")
    if use_rxe:
        lines.append("USE_RXE=1")
    throtl_types = " ".join(t for t in (throtl_blkdev_types or []) if t)
    if throtl_types:
        lines.append(f'THROTL_BLKDEV_TYPES="{throtl_types}"')
    if test_timeout:
        lines.append(f"TEST_TIMEOUT={int(test_timeout)}")
    pairs = " ".join(
        f"{k}:{int(v)}" for k, v in (test_timeouts or {}).items() if k and v
    )
    if pairs:
        lines.append(f'TEST_TIMEOUTS="{pairs}"')
    return "\n".join(lines) + "\n"


def parse_seqres(text: str) -> dict[str, str]:
    """Parse one per-test result file (TSV `key\\tvalue` lines) into a dict.

    `./check` writes one such file per test at `results/<devdir>/<group>/<nnn>`;
    the keys include `status` (`pass`/`fail`/`not run`), `reason`
    (`output`/`exit`/`dmesg`/`kmemleak`, only on a fail; a `not run` row has no
    reason key, its skip reason is stdout-only), `runtime` (like `4.077s`),
    `date`, `description`, and `exit_status`. A missing or truncated file (no
    `status` key survives) degrades to `status` `missing` rather than raising,
    so a killed or hung test can never read as a pass. Lines without a tab are
    skipped.
    """
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition("\t")
        if not sep or not key.strip():
            continue
        out[key.strip()] = value.strip()
    if "status" not in out:
        out["status"] = "missing"
    return out


_RUNTIME_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)s?")


def runtime_seconds(value: str) -> float | None:
    """A seqres `runtime` value (`4.077s`) as seconds, or None when unparseable."""
    m = _RUNTIME_RE.fullmatch((value or "").strip())
    return float(m.group(1)) if m else None


_TEST_NUM_RE = re.compile(r"^[0-9]{3}$")


def collect_group_rows(results_root: Path | str, group: str) -> list[dict]:
    """One row per result file of `<group>`, across every `<devdir>`.

    Walks `results/<devdir>/<group>/<nnn>` under `results_root`: `<devdir>` is
    `nodev` or a device basename, optionally suffixed by a set_conditions
    variant (`nodev_tr_tcp_bd_file`), so one test number yields one row PER
    devdir it ran under; every row is returned, keyed (`devdir`, `group/nnn`).
    The `<nnn>.full`/`.out.bad`/`.dmesg`/`.kmemleak` companions are not result
    files and are skipped. Row shape `{devdir, test, status, reason, runtime}`
    with `runtime` in seconds where parseable; a missing tree returns `[]`
    (the caller's verdict rule treats zero rows as `notrun`, never a pass).
    """
    g = _safe_group(group)
    rows: list[dict] = []
    root = Path(results_root)
    if not root.is_dir():
        return rows
    for devdir in sorted(p for p in root.iterdir() if p.is_dir()):
        group_dir = devdir / g
        if not group_dir.is_dir():
            continue
        for entry in sorted(group_dir.iterdir()):
            if not entry.is_file() or not _TEST_NUM_RE.match(entry.name):
                continue
            try:
                seqres = parse_seqres(entry.read_text())
            except OSError:
                seqres = {"status": "missing"}
            rows.append(
                {
                    "devdir": devdir.name,
                    "test": f"{g}/{entry.name}",
                    "status": seqres.get("status", "missing"),
                    "reason": seqres.get("reason", ""),
                    "runtime": runtime_seconds(seqres.get("runtime", "")),
                }
            )
    return rows


def group_status(
    rows: list[dict], unit_ok: bool, crashed: bool, timed_out: bool
) -> str:
    """One group's verdict from its result rows and its run outcome.

    A crashed guest, a timed-out group, or a unit that did not finish cleanly
    is `failed` even when plausible rows exist. Zero rows is `notrun`: a failed
    `group_requires` prints one line, writes NO files, and exits 0, so an empty
    tree must never pass. Any row that is not `pass` or `not run` (a `fail`, or
    a `missing` truncated file) fails the group; a group whose every row is
    `not run` is `notrun`. A `notrun` group is NOT a pass for `run_status`.
    """
    if crashed or timed_out or not unit_ok:
        return "failed"
    if not rows:
        return "notrun"
    if any(r.get("status") not in ("pass", "not run") for r in rows):
        return "failed"
    if all(r.get("status") == "not run" for r in rows):
        return "notrun"
    return "passed"


def run_status(per_group: list[dict]) -> str:
    """The run verdict from the per-group collect results, the one rule
    `f/blktests/report` and `f/blktests/judge` share: `passed` only when every
    group passed and there was at least one (a `notrun` group is not a pass,
    and a skip_failures error object from a hard step failure is not either);
    aggregating nothing must never read as a pass."""
    ok = bool(per_group) and all(
        isinstance(g, dict) and g.get("status") == "passed" for g in per_group
    )
    return "passed" if ok else "failed"


def groups_cache(vm_name: str, workers: Path | None = None) -> Path:
    """Per-VM cache of the guest's installed blktests enumeration.

    `f/blktests/discover` writes `{groups, tests, devices}` here; the run
    form's pickers read it, since a form dynselect cannot reach the guest
    over vsock.
    """
    return share_dir(vm_name, workers) / "groups.json"


def _cache(vm_name: str) -> dict:
    vm = (vm_name or "").strip()
    if not vm:
        return {}
    try:
        data = json.loads(groups_cache(vm).read_text())
    except Exception:
        return {}
    if isinstance(data, list):
        return {"groups": data}
    return data if isinstance(data, dict) else {}


def _cached_names(cache: Path) -> list[str]:
    try:
        data = json.loads(cache.read_text())
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("groups") or []
    return [g for g in data if isinstance(g, str) and g]


# Static fallback of the upstream test numbers per group (first, last,
# missing), a snapshot of the pinned package so the Tests and Exclude
# pickers are never an empty box; the per-VM cache discover writes wins
# after the first discovery.
_TEST_RANGES: dict[str, tuple[int, int, tuple[int, ...]]] = {
    "bcache": (1, 1, ()),
    "blktrace": (1, 2, ()),
    "block": (1, 46, (13, 26)),
    "dm": (1, 3, ()),
    "loop": (1, 13, ()),
    "md": (1, 4, ()),
    "meta": (1, 24, ()),
    "nbd": (1, 4, ()),
    "nvme": (2, 69, (7, 9, 11, 13, 15, 20, 24)),
    "rnbd": (1, 2, ()),
    "scsi": (1, 11, (3,)),
    "srp": (1, 16, (15,)),
    "throtl": (1, 8, ()),
    "ublk": (1, 6, ()),
    "zbd": (1, 14, ()),
}


def catalog_tests() -> list[str]:
    """Every upstream test name (`group/nnn`), in catalog group order."""
    out: list[str] = []
    for g in group_names():
        first, last, missing = _TEST_RANGES.get(g, (0, -1, ()))
        out += [f"{g}/{n:03d}" for n in range(first, last + 1) if n not in missing]
    return out


# The data disks every bringup guest carries; the safe pre-discovery
# fallback for the TEST_DEVS picker (the guest's root is tmpfs and its
# store is virtiofs, so on these guests the NVMe disks exist only for
# testing).
_FALLBACK_DEVICES = [f"/dev/nvme{i}n1" for i in range(5)]


def _pick(entries: list[dict], filterText: str) -> list[dict]:
    needle = (filterText or "").lower()
    return [
        e
        for e in entries
        if needle in e["value"].lower() or needle in e["label"].lower()
    ]


def list_tests(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_tests` entrypoint: the guest's individual tests.

    Reads the per-VM cache `f/blktests/discover` writes; before the first
    discovery it falls back to the static catalog snapshot. Never raises.
    """
    tests = [t for t in _cache(vm_name).get("tests") or [] if isinstance(t, str)]
    names = tests or catalog_tests()
    return _pick([{"value": t, "label": t} for t in names], filterText)


def list_devices(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_devices` entrypoint for `TEST_DEVS`: the guest's
    NVMe data disks, labeled with their size from discover's enumeration;
    before the first discovery, the canonical guest data-disk paths. Never
    raises."""
    devs = [d for d in _cache(vm_name).get("devices") or [] if isinstance(d, dict)]
    if devs:
        entries = [
            {
                "value": d.get("name", ""),
                "label": f"{d.get('name', '')} ({d.get('size', '?')})",
            }
            for d in devs
            if d.get("name")
        ]
    else:
        entries = [
            {"value": n, "label": f"{n} (guest data disk)"} for n in _FALLBACK_DEVICES
        ]
    return _pick(entries, filterText)


def list_exclude(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_exclude` entrypoint for `EXCLUDE`: whole groups
    first (labeled from the catalog), then every individual test, matching
    the two forms upstream accepts (exact `group` or `group/nnn`). Never
    raises."""
    labels = {g["name"]: f"{g['name']}: whole group" for g in GROUPS}
    groups = [{"value": n, "label": labels[n]} for n in group_names()]
    return _pick(groups, filterText) + list_tests(vm_name, filterText)


def list_groups(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_groups` entrypoint: the guest's blktests groups, named.

    Reads the per-VM cache `f/blktests/discover` writes from the guest's
    installed `tests/*/rc` tree (a form dynselect cannot reach the guest over
    vsock), labeled from the curated catalog (`name: description (N tests)`).
    Before the selected guest's first discovery it falls back to the static
    catalog, so it is never an empty box. Never raises.
    """
    labels = {
        g["name"]: f"{g['name']}: {g['description']} ({g['tests']} tests)"
        for g in GROUPS
    }
    cached: list[str] = []
    vm = (vm_name or "").strip()
    if vm:
        try:
            cached = _cached_names(groups_cache(vm))
        except Exception:
            cached = []
    names = cached or group_names()
    needle = (filterText or "").lower()
    return [
        {"value": n, "label": labels.get(n, n)}
        for n in names
        if needle in n.lower() or needle in labels.get(n, n).lower()
    ]


def main():
    """Library module imported by the f/blktests/* steps; not a runnable step."""
    return "f/blktests/common: blktests share config + TSV result helpers"
