# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Shared library for the f/fstests/* steps (host side of the xfstests run);
# imported as f.fstests.common, not a runnable step. Touches only the host end
# of the rw virtiofs share (tag `fstests`) the guest mounts at /var/lib/xfstests.
#
# The contract with the guest side (kept verbatim on both ends):
#   * guest mount: /var/lib/xfstests (GUEST_STATE_DIR), share tag `fstests`;
#   * <share>/local.config  = the xfstests HOST_OPTIONS config (sections);
#   * <share>/check.env      = systemd EnvironmentFile (HOST_OPTIONS=<guest path>,
#                              XFSTESTS_CHECK_ARGS=<./check flags>);
#   * <share>/<kver>/results/<section>/ = RESULT_BASE, keyed by the guest's kernel
#                              release (unit WorkingDirectory=.../%v); the guest
#                              writes, the host reads.
from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from f.common.devshell import DevShell, Nix, system_dir

# Guest-side constants (the xfstests@.service WorkingDirectory + share mount).
GUEST_STATE_DIR = "/var/lib/xfstests"
GUEST_TAG = "fstests"

# Where the xfstests `-R xunit` report lands inside a section's RESULT_BASE
# subdir (common/report: `out_fn="$REPORT_DIR/result.xml"`).
XUNIT_REPORT = "result.xml"


def _workers() -> Path:
    return Path(os.environ["WORKERS_DIR"])


def share_dir(vm_name: str, workers: Path | None = None) -> Path:
    """Host path of the VM's `fstests` virtiofs share, name-escape hardened.

    `$WORKERS_DIR/shared/fstests/<vm_name>`. Lives under `shared/` so every worker
    sees the same bytes the guest's virtiofsd serves. `vm_name` is resolved and
    checked to sit directly under the share root, so a crafted name (`../x`) can
    never write outside it.
    """
    root = (workers or _workers()) / "shared/fstests"
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


def section_results_dir(vm_name: str, kernel_version: str, section: str,
                        workers: Path | None = None) -> Path:
    """A section's RESULT_BASE subdir on the VM's `fstests` share, keyed by the kernel.

    `<share_dir>/<kver>/results/<section>`: the host view of the guest's
    `/var/lib/xfstests/<kver>/results/<section>`. The unit's `WorkingDirectory=.../%v`
    keys by kernel release (so the same closure, booted into different kernels, never
    clobbers); `results` is the xfstests-check wrapper's single forced `$PWD/results`.
    Path-traversal hardened: a crafted section/kver can't escape the VM's share.
    """
    kv = _safe_kver(kernel_version)
    # Anchor the traversal guard at <kver>/results, not the VM-share root: `section`
    # is not run through _safe_kver and `[header]` names admit `/` and `..`, so a
    # crafted section (e.g. `../../OTHER_KVER/results/x`) must not be able to reach a
    # sibling kernel's tree; clean_results would otherwise rmtree it.
    base = (share_dir(vm_name, workers) / kv / "results").resolve()
    path = (base / section).resolve()
    if path != base and base not in path.parents:
        raise ValueError(f"section {section!r} / kver {kernel_version!r} resolves outside {base}")
    return path


def _atomic_write(path: Path, data: str, mode: int = 0o644) -> None:
    """Write via a hidden temp file + rename so a concurrent reader on the shared
    dir (the guest's virtiofsd) never sees a half-written `local.config`/`check.env`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def render_local_config(local_config: str, devices: list[dict] | None = None) -> str:
    """The xfstests `HOST_OPTIONS` config text (`local.config`).

    A non-empty `local_config` is returned verbatim. When empty, synthesize a
    minimal single-section device base from `devices` (`TEST_DEV`/`SCRATCH_DEV`
    from the first two, the rest as `SCRATCH_DEV_POOL`, fixed `TEST_DIR`/`SCRATCH_MNT`).
    """
    text = (local_config or "").strip()
    if text:
        return text if text.endswith("\n") else text + "\n"
    devs = [d["dev"] if isinstance(d, dict) else str(d) for d in (devices or [])]
    if not devs:
        raise ValueError("render_local_config: empty local_config and no devices to synthesize from")
    lines = ["[default]", "FSTYP=xfs", f"TEST_DEV={devs[0]}", "TEST_DIR=/media/test"]
    if len(devs) >= 2:
        lines.append(f"SCRATCH_DEV={devs[1]}")
    lines.append("SCRATCH_MNT=/media/scratch")
    if len(devs) > 2:
        lines.append(f"SCRATCH_DEV_POOL=\"{' '.join(devs[2:])}\"")
    return "\n".join(lines) + "\n"


# Mount points f/fstests/prepare creates in the guest for TEST_DIR / SCRATCH_MNT.
MEDIA_TEST = "/media/test"
MEDIA_SCRATCH = "/media/scratch"

# XFS geometry ranges, from xfsprogs 6.19.0 + kernel libxfs/xfs_types.h.
# Block size: 2^9 .. 2^16; sector size: 2^9 .. 2^15 (mkfs.xfs rejects -s size=65536);
# constraint sector <= block. V5/crc needs block >= XFS_MIN_CRC_BLOCKSIZE, so block
# 512 is reachable only with crc=0 (V4).
XFS_BLOCK_SIZES = [512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]
XFS_SECTOR_SIZES = [512, 1024, 2048, 4096, 8192, 16384, 32768]
XFS_MIN_CRC_BLOCKSIZE = 1024

# V4 (crc=0) XFS only mounts when its block size is <= the guest page size: the kernel
# refuses a larger-block V4 fs at mount ("V4 Filesystem with blocksize N bytes. Only
# pagesize (M) or less is supported."), even though mkfs.xfs will still create it. V5
# (crc=1) carries large-block support and has no such limit. x86_64 guests are 4 KiB.
GUEST_PAGE_SIZE = 4096

# Single-knob XFS feature variants, each orthogonal to geometry. `v4` marks the
# V4 (nocrc) layout, which alone reaches block size 512. Insertion order is the
# feature order in the generated matrix.
XFS_FEATURES: dict[str, dict] = {
    "": {"mkfs": "", "mount": "", "v4": False},
    # Combined "modes" (verified against mkfs.xfs 7.0.1 + a live v7.1 mount): `full`
    # turns every compatible V5 feature on at once (metadir + the default-on reflink/
    # rmapbt/finobt/inobtcount/sparse/bigtime/nrext64) plus all three quota types
    # (group⊕project exclusivity was lifted on recent kernels): for feature-interaction
    # coverage the isolated sections miss. `nofeat` is the inverse: a V5 fs with every
    # optional feature off. asciici is excluded from `full` (case-insensitive lookup
    # would spuriously fail case-sensitive tests); nocrc (V4) cannot join either (V5
    # features need crc); realtime/logdev need external devices; all stay separate.
    "full": {"mkfs": "-m metadir=1", "mount": "-o usrquota,grpquota,prjquota", "v4": False},
    "nofeat": {"mkfs": "-m reflink=0,rmapbt=0,finobt=0,inobtcount=0,bigtime=0 -i sparse=0,nrext64=0",
               "mount": "", "v4": False},
    "quota": {"mkfs": "", "mount": "-o usrquota,grpquota", "v4": False},
    "prjquota": {"mkfs": "", "mount": "-o prjquota", "v4": False},
    "noreflink": {"mkfs": "-m reflink=0", "mount": "", "v4": False},
    "normapbt": {"mkfs": "-m rmapbt=0", "mount": "", "v4": False},
    "metadir": {"mkfs": "-m metadir=1", "mount": "", "v4": False},
    # OFF-toggle V5 features: each disables a default-on geometry knob. inobtcount
    # requires finobt, so nofinobt turns both off together (mkfs errors otherwise).
    "nofinobt": {"mkfs": "-m finobt=0,inobtcount=0", "mount": "", "v4": False},
    "noinobtcount": {"mkfs": "-m inobtcount=0", "mount": "", "v4": False},
    "nosparse": {"mkfs": "-i sparse=0", "mount": "", "v4": False},
    "nobigtime": {"mkfs": "-m bigtime=0", "mount": "", "v4": False},
    "nonrext64": {"mkfs": "-i nrext64=0", "mount": "", "v4": False},
    "asciici": {"mkfs": "-n version=ci", "mount": "", "v4": False},
    # Tier-2 external-device features: `needs` names the dedicated SCRATCH external
    # device (logdev/rtdev) the injector binds. realtime turns reflink off (rt
    # reflink needs metadir); realtime_reflink re-enables it via metadir and needs
    # block >= XFS_MIN_RTEXTSIZE (4096) or mkfs silently drops reflink.
    "logdev": {"mkfs": "", "mount": "", "v4": False, "needs": "logdev"},
    "realtime": {"mkfs": "-m reflink=0", "mount": "", "v4": False, "needs": "rtdev"},
    "realtime_reflink": {"mkfs": "-m metadir=1", "mount": "", "v4": False,
                         "needs": "rtdev", "min_block": 4096},
    "nocrc": {"mkfs": "-m crc=0", "mount": "", "v4": True},
}


def _size_tag(n: int) -> str:
    """A byte size as a section-name tag: `512 -> "512"`, otherwise KiB (`4096 -> "4k"`)."""
    return "512" if n == 512 else f"{n // 1024}k"


def xfs_feature_names() -> list[str]:
    """The selectable feature names for the run form: `all` (the whole cross-product),
    `default` (the plain V5 fs, the `""` feature), then each named feature in order."""
    return ["all", "default", *(f for f in XFS_FEATURES if f)]


def _feature_keys(feature: str | None) -> list[str]:
    """Resolve a selector value to the `XFS_FEATURES` keys it covers: `all`/None/`""`
    -> every feature; `default` -> the plain `""` feature; else the named one."""
    if feature in (None, "", "all"):
        return list(XFS_FEATURES)
    key = "" if feature == "default" else feature
    if key not in XFS_FEATURES:
        raise ValueError(f"unknown feature {feature!r}; choose from {xfs_feature_names()}")
    return [key]


def xfs_profiles_matrix(feature: str | None = None, geometry: str = "matrix") -> dict[str, dict[str, str]]:
    """The feature [x block x sector] cross-product of XFS profiles, in order.

    `feature` narrows to one feature's matrix (`default` = the plain `""` feature,
    `all`/None = every feature); see `xfs_feature_names`. `geometry="default"` drops
    the block/sector matrix entirely: one section per feature at mkfs's default
    geometry (`xfs[_<feat>]`, no `-b`/`-s`), the short/auditable form; `geometry=
    "matrix"` (default) is the full cross-product. For the matrix: each `block` in
    `XFS_BLOCK_SIZES`, each `sector` in `XFS_SECTOR_SIZES` with `sector <= block`; a V5
    (non-`nocrc`) feature skips `block < XFS_MIN_CRC_BLOCKSIZE` (V5 needs block >= 1024);
    `nocrc` (V4) includes block 512 but is capped at `GUEST_PAGE_SIZE` (large block size
    is V5-only; a V4 fs above the page size is unmountable); a `min_block` feature skips
    `block < min_block` (rt-reflink needs >= 4096). Section name
    `xfs_[<feat>_]bs<block-tag>_ss<sector-tag>`.
    Value shape `{"mkfs", "mount"}`, plus `"needs"` (logdev/rtdev) for an external feature.
    """
    if geometry not in ("matrix", "default"):
        raise ValueError(f"unknown geometry {geometry!r}; choose 'matrix' or 'default'")
    matrix: dict[str, dict[str, str]] = {}
    for feat in _feature_keys(feature):
        feature_def = XFS_FEATURES[feat]
        if geometry == "default":
            name = "xfs" + (f"_{feat}" if feat else "")
            value = {"mkfs": feature_def["mkfs"], "mount": feature_def["mount"]}
            if "needs" in feature_def:
                value["needs"] = feature_def["needs"]
            matrix[name] = value
            continue
        min_block = feature_def.get("min_block")
        for block in XFS_BLOCK_SIZES:
            if not feature_def["v4"] and block < XFS_MIN_CRC_BLOCKSIZE:
                continue
            # Large block size (block > page size) is a V5-only capability; a V4 (crc=0)
            # filesystem above the page size is unmountable (the kernel rejects it),
            # though mkfs would create it. Don't emit those profiles at all.
            if feature_def["v4"] and block > GUEST_PAGE_SIZE:
                continue
            if min_block is not None and block < min_block:
                continue
            for sector in XFS_SECTOR_SIZES:
                if sector > block:
                    continue
                name = "xfs_" + (feat + "_" if feat else "") + \
                    f"bs{_size_tag(block)}_ss{_size_tag(sector)}"
                parts = [feature_def["mkfs"], f"-b size={block}", f"-s size={sector}"]
                value: dict[str, str] = {
                    "mkfs": " ".join(p for p in parts if p),
                    "mount": feature_def["mount"],
                }
                if "needs" in feature_def:
                    value["needs"] = feature_def["needs"]
                matrix[name] = value
    return matrix


# Device-agnostic, FSTYP=xfs, one self-contained xfstests section each.
# xfs_catalog_text renders them as the shipped, editable local.config default;
# render_config injects the discovered devices per section at render time.
# Insertion order is the catalog order (the run-form section list and the
# per-section forloop).
XFS_PROFILES: dict[str, dict[str, str]] = xfs_profiles_matrix()


def xfs_profiles() -> list[str]:
    """The XFS profile names, in catalog order (run-form enum + validation)."""
    return list(XFS_PROFILES)


def xfs_catalog_text(feature: str | None = None, geometry: str = "matrix") -> str:
    """The XFS profile matrix rendered as a device-agnostic xfstests config.

    `feature` narrows to one feature's bs/ss matrix (`default`/`full`/`nofeat`/`quota`
    /...), keeping each generated `local.config` small; `all`/None is the whole
    cross-product. `geometry="default"` drops the bs/ss matrix: one default-geometry
    section per feature (the shortest, most auditable form). One `[name]` block per
    profile in catalog order, each `FSTYP=xfs`
    plus the profile's `MKFS_OPTIONS`/`MOUNT_OPTIONS` when set, sections separated by a
    blank line. An external-device profile (value has `needs`) also emits
    `USE_EXTERNAL=yes` (the xfstests var) and `# external=<needs>` (the
    device-agnostic marker the injector reads; xfstests ignores `#` lines).
    Device-agnostic by design: no `TEST_DEV`/`SCRATCH_DEV`, those are injected
    per selected section at render time.
    """
    profiles = (XFS_PROFILES if feature in (None, "all") and geometry == "matrix"
                else xfs_profiles_matrix(feature, geometry))
    blocks: list[str] = []
    for name, profile in profiles.items():
        lines = [f"[{name}]", "FSTYP=xfs"]
        if profile["mkfs"]:
            lines.append(f'MKFS_OPTIONS="{profile["mkfs"]}"')
        if profile["mount"]:
            lines.append(f'MOUNT_OPTIONS="{profile["mount"]}"')
        if profile.get("needs"):
            lines.append("USE_EXTERNAL=yes")
            lines.append(f"# external={profile['needs']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


_BLOCK_SIZE_RE = re.compile(r"-b\s+size=(\d+)")


def section_block_block_size(block: str, section: str) -> int:
    """The filesystem block size of one section's verbatim `block` text, in bytes.

    Parses `-b size=<N>` from the block's `MKFS_OPTIONS` (via `section_vars`);
    a section with no block-size mkfs option defaults to 4096 (`mkfs.xfs`'s default
    on a >= 4K-sector device). Used to gate a sub-sector section out of a run.
    """
    mkfs = section_vars(block, section).get("MKFS_OPTIONS", "")
    m = _BLOCK_SIZE_RE.search(mkfs)
    return int(m.group(1)) if m else 4096


_SECTOR_SIZE_RE = re.compile(r"-s\s+size=(\d+)")


def section_sector_size(block: str, section: str) -> int | None:
    """The sector size of one section's verbatim `block` text, in bytes, or `None`.

    Parses `-s size=<N>` from the block's `MKFS_OPTIONS` (via `section_vars`).
    Unlike block size there is no safe default to assume; `None` means "no explicit
    sector size", which the caller treats as "unknown, don't gate on sector".
    """
    mkfs = section_vars(block, section).get("MKFS_OPTIONS", "")
    m = _SECTOR_SIZE_RE.search(mkfs)
    return int(m.group(1)) if m else None


def section_is_v4(block: str, section: str) -> bool:
    """True when the section's `MKFS_OPTIONS` selects the V4 (crc=0) layout, whose
    block size must not exceed the page size to be mountable: see `GUEST_PAGE_SIZE`."""
    return "crc=0" in section_vars(block, section).get("MKFS_OPTIONS", "")


def section_config(vm_name: str, section: str, workers: Path | None = None) -> dict:
    """The filesystem-under-test geometry for `<section>`, from its rendered
    `<share>/<vm>/<section>.config` (FSTYP + mkfs/mount options + block/sector size).
    Host-side, read-only; `{}` when the config is absent (section never rendered).
    This is the *configured* geometry; the realized `xfs_info` feature set needs a
    guest query, which this does not do.
    """
    cfg = share_dir(vm_name, workers) / f"{section}.config"
    if not cfg.is_file():
        return {}
    text = cfg.read_text()
    v = section_vars(text, section)
    return {
        "fstype": v.get("FSTYP", ""),
        "mkfs_options": v.get("MKFS_OPTIONS", ""),
        "mount_options": v.get("MOUNT_OPTIONS", ""),
        "bsize": section_block_block_size(text, section),
        "sectsize": section_sector_size(text, section),
    }


def parse_xfs_info(text: str) -> dict[str, str]:
    """Parse `xfs_info` output into a flat `key=value` map (e.g. `crc`, `reflink`,
    `rmapbt`, `bigtime`, `finobt`, `sparse`, `ascii-ci`, `lazy-count`, `bsize`, `sectsz`).

    The key class is `[\\w-]+`, not `\\w+`, so hyphenated keys (`ascii-ci`, `lazy-count`,
    `meta-data`) parse whole; `xfs_report_geom()` (libfrog/fsgeom.c) emits both. The
    output repeats some numeric keys across sections (`bsize`, `blocks`, `sunit`, `sectsz`,
    `version`); a later one overwrites, so those numerics are NOT section-accurate here;
    only the boolean feature flags (each key unique) are reliable, which is all the report
    surfaces. Returns whatever tokens are present: `{}` for empty input."""
    out: dict[str, str] = {}
    for key, value in re.findall(r"([\w-]+)=([^\s,]+)", text or ""):
        out[key] = value
    return out


def read_xfs_info(vm_name: str, section: str, workers: Path | None = None) -> dict:
    """The realized `xfs_info` for `<section>`, captured by `f/fstests/prepare` to
    `<share>/<vm>/<section>.xfs_info`. Returns `{"raw": <text>, "features": {...}}`, or
    `{}` when absent (non-xfs section, or the capture was skipped/failed). Host-side."""
    path = share_dir(vm_name, workers) / f"{section}.xfs_info"
    if not path.is_file():
        return {}
    text = path.read_text()
    return {"raw": text, "features": parse_xfs_info(text)}


def device_sector(devices: list[dict] | list[str]) -> int:
    """The max logical sector size across `devices`, in bytes (the minimum block
    size `mkfs.xfs` enforces). Defaults to 512 when a device omits `log_sec`;
    bare-string device lists (no dict) assume 512.
    """
    sizes = [int(d["log_sec"]) for d in devices if isinstance(d, dict) and "log_sec" in d]
    return max(sizes) if sizes else 512


def _device_names(devices: list[dict] | list[str]) -> list[str]:
    """The device node paths from the discover shape (`[{name, size, log_sec}]`) or bare strings."""
    return [d["name"] if isinstance(d, dict) else str(d) for d in devices]


_EXTERNAL_RE = re.compile(r"^\s*#\s*external=(logdev|rtdev)\s*$", re.MULTILINE)


def section_external(block: str) -> str | None:
    """`"logdev"`/`"rtdev"` if the block carries a `# external=<dev>` marker, else `None`.

    The marker is the device-agnostic signal `xfs_catalog_text` emits for an
    external-device profile; xfstests ignores the `#` line, the injector reads it
    to bind a dedicated SCRATCH external device instead of a pool.
    """
    m = _EXTERNAL_RE.search(block or "")
    return m.group(1) if m else None


def inject_device_base(block: str, devices: list[dict] | list[str], logwrites: bool = False) -> str:
    """Append the discovered-device base lines to one section's verbatim `block`.

    Binds a device-agnostic section to a guest's discovered devices. With no
    `# external=` marker (the common path): `TEST_DEV` from the first, and the rest
    as scratch: a single `SCRATCH_DEV` with exactly two devices, or a
    `SCRATCH_DEV_POOL` of all the extras with more. `SCRATCH_DEV` and
    `SCRATCH_DEV_POOL` are mutually exclusive (xfstests `common/config` errors if both
    are set); with a pool, `check` derives `SCRATCH_DEV` from its first element.

    With a `# external=logdev`/`# external=rtdev` marker the section needs a
    dedicated external device: `TEST_DEV=devs[0]`, a single `SCRATCH_DEV=devs[1]`,
    and the external device on `devs[2]` (`SCRATCH_LOGDEV` or `SCRATCH_RTDEV`); no
    pool, and `USE_EXTERNAL` is left to the catalog (the marker is what matters).
    Needs >= 3 devices.

    Each base line is added only when its key is not already in the block, so an
    advanced user who hardcoded a device keeps it (we never override), and we add no
    scratch base at all if the section already sets either scratch key. `devices` is
    the discover `[{name, size}]` shape or bare device strings; the non-external path
    needs >= 2. `logwrites` reserves the last device as `LOGWRITES_DEV` (the
    dm-log-writes replay log) before TEST/SCRATCH bind, needing one extra device.
    Returns the augmented block with a trailing newline.
    """
    devs = _device_names(devices)
    external = section_external(block)
    # LOGWRITES_DEV is dm-log-writes test infrastructure (the generic/45x crash-
    # consistency replay log), not an fs feature: carve the last device for it
    # before TEST/SCRATCH bind from the rest, so it applies to every section. A
    # LOGWRITES_DEV hardcoded in the block wins; needs one device beyond the
    # test/scratch(+external) minimum.
    logwrites_line: tuple[str, str] | None = None
    if logwrites and "LOGWRITES_DEV=" not in block:
        need = (3 if external else 2) + 1
        if len(devs) < need:
            raise ValueError(
                f"inject_device_base: need >= {need} devices for TEST_DEV + SCRATCH_DEV"
                f"{' + external ' + external if external else ''} + LOGWRITES_DEV, "
                f"got {len(devs)}"
            )
        logwrites_line = ("LOGWRITES_DEV=", f"LOGWRITES_DEV={devs[-1]}")
        devs = devs[:-1]
    if external:
        if len(devs) < 3:
            raise ValueError(
                f"inject_device_base: need >= 3 devices for TEST_DEV + SCRATCH_DEV + "
                f"an external {external}, got {len(devs)}"
            )
        base = [
            ("TEST_DEV=", f"TEST_DEV={devs[0]}"),
            ("TEST_DIR=", f"TEST_DIR={MEDIA_TEST}"),
            ("SCRATCH_MNT=", f"SCRATCH_MNT={MEDIA_SCRATCH}"),
        ]
        if "SCRATCH_DEV=" not in block and "SCRATCH_DEV_POOL=" not in block:
            base.append(("SCRATCH_DEV=", f"SCRATCH_DEV={devs[1]}"))
        ext_key = "SCRATCH_LOGDEV" if external == "logdev" else "SCRATCH_RTDEV"
        base.append((f"{ext_key}=", f"{ext_key}={devs[2]}"))
        if logwrites_line:
            base.append(logwrites_line)
        body = block.rstrip("\n")
        added = [line for key, line in base if key not in block]
        if added:
            body = body + "\n" + "\n".join(added)
        return body + "\n"
    if len(devs) < 2:
        raise ValueError(
            f"inject_device_base: need >= 2 devices for TEST_DEV + SCRATCH_DEV, "
            f"got {len(devs)}"
        )
    base = [
        ("TEST_DEV=", f"TEST_DEV={devs[0]}"),
        ("TEST_DIR=", f"TEST_DIR={MEDIA_TEST}"),
        ("SCRATCH_MNT=", f"SCRATCH_MNT={MEDIA_SCRATCH}"),
    ]
    if "SCRATCH_DEV=" not in block and "SCRATCH_DEV_POOL=" not in block:
        if len(devs) > 2:
            base.append(("SCRATCH_DEV_POOL=", f'SCRATCH_DEV_POOL="{" ".join(devs[1:])}"'))
        else:
            base.append(("SCRATCH_DEV=", f"SCRATCH_DEV={devs[1]}"))
    if logwrites_line:
        base.append(logwrites_line)
    body = block.rstrip("\n")
    added = [line for key, line in base if key not in block]
    if added:
        body = body + "\n" + "\n".join(added)
    return body + "\n"


_SECTION_RE = re.compile(r"^\[([^\]]+)\]")


def parse_sections(local_config_text: str) -> list[str]:
    """The `[section]` header names of an xfstests config, in file order.

    These drive the flow's sequential forloop (one `xfstests@<section>.service`
    per section). A section name is `[name]` at the start of a line (matching the
    README.config-sections syntax); duplicates collapse to first occurrence.
    """
    seen: list[str] = []
    for line in (local_config_text or "").splitlines():
        m = _SECTION_RE.match(line.strip())
        if m:
            name = m.group(1).strip()
            if name and name not in seen:
                seen.append(name)
    return seen


def _strip_quotes(value: str) -> str:
    """Drop one matching pair of surrounding quotes from an xfstests config value.

    README.config-sections allows `'` or `"` only at the start and end of a value
    (`KEY="-q -F -b4096"`); systemd/shell would unquote them, so we do too.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def section_vars(config_text: str, section: str) -> dict[str, str]:
    """The xfstests config vars for `<section>`, matching `./check`'s sourcing:
    with sections only that block, with none the whole file. `#` comments and
    blanks are skipped; one pair of surrounding quotes is stripped from each value.
    Returns whatever keys are present; nothing is hardcoded or defaulted.
    """
    has_sections = bool(parse_sections(config_text))
    out: dict[str, str] = {}
    current: str | None = None
    for raw in (config_text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _SECTION_RE.match(line)
        if m:
            current = m.group(1).strip()
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = _strip_quotes(value)
        if (not has_sections and current is None) or current == section:
            out[key] = value
    return out


def section_block(config_text: str, section: str) -> str:
    """The raw `[section]` block (header through the line before the next `[section]`
    or EOF), verbatim. Written out as a one-section config so `./check -s <section>`
    resolves it on its own: with several sections in one file, check sets up TEST_DEV
    with the wrong FSTYP before the selected section applies.
    """
    out: list[str] = []
    grabbing = False
    for line in (config_text or "").splitlines():
        m = _SECTION_RE.match(line.strip())
        if m:
            if grabbing:
                break
            grabbing = m.group(1).strip() == section
            if grabbing:
                out.append(line)
            continue
        if grabbing:
            out.append(line)
    return "\n".join(out).rstrip() + "\n" if out else ""


def build_check_args(
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
) -> str:
    """Compose the verbatim xfstests `./check` flag string (no `-s`).

    Maps the typed inputs to `./check`'s own short flags (xfstests has no long
    forms; these short flags are the upstream vocabulary, kept as-is):
    `-g <group>`, `-x <exclude_group>`, `-X <exclude_file>`, `-R <fmt>` (default
    `xunit`), `-r` (randomize). `iterations` > 1 emits `-i <n>` (or `-I <n>` when
    `stop_on_fail`, the default, to stop iterating at the first failure), which reruns
    the WHOLE test list n times (each test runs n times, interleaved across n passes
    with fresh setup, not n in a row), so the xunit gets up to n testcases per test.
    `loop_on_fail` > 0 emits `-L <n>`, which reruns only a FAILED test up to n more
    times and prints its aggregate pass/fail % (the flaky-test quantifier; the % is
    in the log, not the xunit).

    `test_selection` enforces a mutual exclusion the bare `./check` does NOT: xfstests
    runs the UNION of `-g <group>` and any trailing testlist, so the two together would
    expand the group and swamp an explicit list. We expose only one mode at a time:
    `groups` emits `-g <comma-joined groups>` (`-x`/exclude_group applies) and ignores
    `tests`; `tests` emits the positional testlist and ignores `groups`/`exclude_group`.
    The systemd unit supplies `-s %i`, so this never emits a section flag; the result
    becomes `$XFSTESTS_CHECK_ARGS`, word-split by systemd.
    """
    in_tests_mode = test_selection == "tests"
    group = "" if in_tests_mode else ",".join(g for g in (groups or []) if g)
    args: list[str] = []
    if group:
        args += ["-g", group]
    if exclude_group and not in_tests_mode:
        args += ["-x", exclude_group]
    if exclude:
        args += ["-X", exclude]
    if report:
        args += ["-R", report]
    if randomize:
        args.append("-r")
    if iterations and int(iterations) > 1:
        # -I iterates but stops at the first failure; -i runs every pass. Independent of
        # -L below: xfstests' istop is checked per iteration regardless of loop_on_fail.
        args += ["-I" if stop_on_fail else "-i", str(int(iterations))]
    if loop_on_fail and int(loop_on_fail) > 0:
        args += ["-L", str(int(loop_on_fail))]
    if in_tests_mode and tests:
        args += tests.split()
    return " ".join(args)


def render_check_env(host_options: str, check_args: str, test_timeout: int = 0,
                     test_timeouts: dict[str, int] | None = None) -> str:
    """The systemd `EnvironmentFile` text the `xfstests@<section>.service` reads:
    `HOST_OPTIONS=<absolute guest path>` and `XFSTESTS_CHECK_ARGS=<./check flags>`.
    RESULT_BASE is omitted; the `xfstests-check` wrapper forces it.

    The per-test watchdog vars the patched `check` reads are added when set:
    `TEST_TIMEOUT=<seconds>` (global, applied as each test's scope `RuntimeMaxSec`;
    0/unset = no limit) and `TEST_TIMEOUTS=<seq:sec ...>` (per-test overrides).
    """
    lines = [f"HOST_OPTIONS={host_options}", f"XFSTESTS_CHECK_ARGS={check_args}"]
    if test_timeout:
        lines.append(f"TEST_TIMEOUT={int(test_timeout)}")
    pairs = " ".join(f"{k}:{int(v)}" for k, v in (test_timeouts or {}).items() if k and v)
    if pairs:
        lines.append(f"TEST_TIMEOUTS={pairs}")
    return "\n".join(lines) + "\n"


def _testcase_name(case: ET.Element) -> str:
    return case.get("name") or case.get("classname") or "?"


# A prior run's `.out.bad` kept by render_config's rotation is named
# `<test>.<NNNN>.out.bad`; exclude those so a re-run's report lists only its own.
_ROTATED_OUT_BAD = re.compile(r"\.\d+\.out\.bad$")


def parse_xunit(results_dir: Path, section: str | None = None) -> dict:
    """Parse the xfstests `-R xunit` report into a result summary.

    xfstests writes `result.xml` per section at `$RESULT_BASE/$section/result.xml`
    (common/report `_xunit_make_section_report`, check `REPORT_DIR=$RESULT_BASE/
    $section`). `results_dir` is the section's RESULT_BASE subdir. Reads the
    `<testsuite>` counters and the per-`<testcase>` `<failure>`/`<skipped>`
    children, and surfaces any `.out.bad` and `check.log` siblings xfstests leaves
    in the same dir. Robust to a missing or unparseable report: returns zeros plus
    `report_present`/`error` flags so a still-running or crashed section degrades
    gracefully.
    """
    report = results_dir / XUNIT_REPORT
    base = {
        "section": section,
        "report": str(report),
        "report_present": report.is_file(),
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "tests": 0,
        "failures": [],
        "notruns": [],
        "per_test": [],
        "out_bad": sorted(str(p) for p in results_dir.glob("**/*.out.bad")
                          if not _ROTATED_OUT_BAD.search(p.name)) if results_dir.is_dir() else [],
        "check_log": str(results_dir / "check.log") if (results_dir / "check.log").is_file() else None,
    }
    if not report.is_file():
        base["error"] = "no xunit report"
        return base
    try:
        root = ET.parse(report).getroot()
    except ET.ParseError as e:
        base["error"] = f"unparseable xunit report: {e}"
        return base

    # The `<testsuite>` header counters (tests/failures/skipped) describe only the
    # FINAL pass; under `-i <n>` xfstests rewrites the header each pass but APPENDS a
    # `<testcase>` per test per pass, so a header `failures="2"` hides a test that
    # failed in earlier passes yet passed the last. Derive the totals from the body
    # instead, one aggregated status per DISTINCT test across all passes: a test is
    # `failed` if it failed any pass, `notrun` if every pass skipped it, else `passed`.
    # This is identical to the header on a single-pass run and truthful on an `-i` run.
    fail_agg: dict[str, dict] = {}
    runs: dict[str, int] = {}
    skips: dict[str, int] = {}
    times: dict[str, float] = {}
    # Elements are in no namespace (the unit header declares only xsi), so a bare
    # local-name match works; be defensive about a namespaced variant anyway.
    for case in root.iter():
        tag = case.tag.rsplit("}", 1)[-1]
        if tag != "testcase":
            continue
        name = _testcase_name(case)
        runs[name] = runs.get(name, 0) + 1
        try:
            times[name] = times.get(name, 0.0) + float(case.get("time") or 0)
        except (TypeError, ValueError):
            pass
        case_failed = case_skipped = False
        for child in case:
            ctag = child.tag.rsplit("}", 1)[-1]
            if ctag == "failure":
                case_failed = True
                entry = fail_agg.get(name)
                # Collapse the N failing passes of one test to a single entry, keeping
                # the failing-pass count (`fails`); first-seen message/type wins.
                if entry:
                    entry["fails"] += 1
                else:
                    fail_agg[name] = {"name": name, "message": child.get("message", ""),
                                      "type": child.get("type", ""), "fails": 1}
            elif ctag == "skipped":
                case_skipped = True
        if case_skipped and not case_failed:
            skips[name] = skips.get(name, 0) + 1

    failures = list(fail_agg.values())
    # Per-test status across passes: failed > notrun (all passes skipped) > passed.
    failed_names = set(fail_agg)
    notrun_set = {n for n in runs
                  if n not in failed_names and skips.get(n, 0) == runs[n]}
    notrun_names = sorted(notrun_set)
    tests = len(runs)
    failed = len(failed_names)
    skipped = len(notrun_names)

    # One row per DISTINCT test (across all `-i` passes), the source for the report's
    # per-section table. `runs` is how many passes the test got; `fails` how many it
    # failed; `time` the summed wall-clock seconds; `message` the first failing diff.
    # Ordered failed → notrun → passed, then by name, so failures sit at the top.
    _rank = {"failed": 0, "notrun": 1, "passed": 2}
    per_test = []
    for name in runs:
        if name in failed_names:
            status, fails, message = "failed", fail_agg[name]["fails"], fail_agg[name]["message"]
        elif name in notrun_set:
            status, fails, message = "notrun", 0, ""
        else:
            status, fails, message = "passed", 0, ""
        per_test.append({"test": name, "status": status, "runs": runs[name],
                         "fails": fails, "time": round(times.get(name, 0.0)),
                         "message": message})
    per_test.sort(key=lambda r: (_rank[r["status"]], r["test"]))

    base.update({
        "passed": max(tests - failed - skipped, 0),
        "failed": failed,
        "skipped": skipped,
        "tests": tests,
        # Max passes any single test got; >1 means an `-i <n>` run (so `fails` reads
        # out of this). 1 (or 0 for an empty report) on an ordinary single-pass run.
        "iterations": max(runs.values(), default=0),
        "failures": failures,
        "notruns": notrun_names,
        "per_test": per_test,
    })
    return base


_CID_RE = re.compile(r"^\s*HostName\s+vsock/(\d+)\s*$")


class RemoteSystemd:
    """Drive a booted guest's `systemd` over vsock-SSH, from the vm worker.

    Every guest command is one explicit `ssh` argv: the options are passed on the
    command line (not hidden in a config file or a SYSTEMD_SSH wrapper), so the
    runner logs the exact, copy-pasteable invocation. `ssh` and `systemd-ssh-proxy`
    come from the `#systemd` devShell; the vsock cid is read from `f/qsu/boot`'s
    `system/ssh/config.d/<vm>.conf`, or supplied explicitly. `systemctl`/`journalctl`
    run in the guest over that `ssh`.

    Equivalent command, against the guest over vsock-SSH:

        ssh -o ProxyCommand='<proxy> %h %p' -o User=root -o IdentityFile=<key> \
            vsock/<cid> <args>
    """

    def __init__(self, workers: Path, vm_name: str, cid: int | None = None) -> None:
        self._vm = vm_name
        self._shell = DevShell(workers, shell="systemd")
        self._key = system_dir() / "ssh/id_ed25519"
        # Read OUR managed ssh config instead of the worker container's /etc/ssh/
        # ssh_config (which carries a GSSAPIAuthentication option the devShell's ssh
        # build rejects, warning on every call). -F makes the system config ignored;
        # our explicit -o below still take precedence. Fall back to /dev/null if the
        # managed config is absent (workbench not initialised).
        config = system_dir() / "ssh/config"
        self._config = str(config) if config.is_file() else "/dev/null"
        self._cid = cid if cid is not None else self._resolve_cid(vm_name)
        if self._cid is None:
            conf = system_dir() / "ssh/config.d" / f"{vm_name}.conf"
            raise ValueError(
                f"no vsock cid for {vm_name!r}: pass cid= or boot the VM so "
                f"{conf} carries HostName vsock/<cid>"
            )
        self._proxy = self._resolve_proxy()

    @staticmethod
    def _resolve_cid(vm_name: str) -> int | None:
        """Parse the vsock cid from `f/qsu/boot`'s `$SYSTEM_DIR/ssh/config.d/<vm>.conf`."""
        conf = system_dir() / "ssh/config.d" / f"{vm_name}.conf"
        if not conf.is_file():
            return None
        for line in conf.read_text().splitlines():
            m = _CID_RE.match(line)
            if m:
                return int(m.group(1))
        return None

    def _resolve_proxy(self) -> Path:
        """`systemd-ssh-proxy`, the sibling of `systemctl`'s bin in the `#systemd` devShell."""
        out = self._shell.capture("sh", "-c", "command -v systemctl").strip()
        if not out:
            raise RuntimeError("systemctl not found in the #systemd devShell")
        return Path(out).resolve().parent.parent / "lib/systemd/systemd-ssh-proxy"

    def _ssh_argv(self, *args: str) -> tuple[str, ...]:
        """The explicit `ssh` argv dialing the guest's AF_VSOCK with the kdevops key.

        `ssh` concatenates its remote-command arguments with spaces and hands the
        result to the guest's login shell *unquoted*, so passing `args` as separate
        argv lets the remote shell re-split on any metacharacter they contain (a
        journal cursor's `;`, a `bash -c` script's spaces). Instead we pre-join with
        `shlex.join` and pass the single quoted string: the remote shell parses it
        back into exactly `args`: bare tokens (`+`, `_TRANSPORT=kernel`) stay bare,
        only metacharacter-bearing tokens get quoted.
        """
        return (
            "ssh",
            "-F", self._config,
            "-o", "LogLevel=ERROR",
            "-o", f"ProxyCommand={self._proxy} %h %p",
            "-o", "ProxyUseFdpass=yes",
            "-o", f"IdentityFile={self._key}",
            "-o", "IdentitiesOnly=yes",
            "-o", "User=root",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            f"vsock/{self._cid}", shlex.join(args),
        )

    def ssh(self, *args: str, capture: bool = True, check: bool = True, quiet: bool = False):
        """Run `<args>` in the guest over the vsock-SSH transport.

        Logs the terse `+ ssh <vm> <args>` (the `nix develop … --command ssh -o … -o …`
        wrapper is constant boilerplate, so the devShell dispatch is logged quietly);
        `quiet` drops even that line, for the repeated polls of a wait/reboot loop.
        """
        if not quiet:
            print(f"+ ssh {self._vm} {shlex.join(args)}", flush=True)
        argv = self._ssh_argv(*args)
        if capture:
            return self._shell.capture(*argv, check=check, quiet=True)
        return self._shell.run(*argv, check=check, quiet=True)

    def systemctl(self, *args: str, capture: bool = False, check: bool = True, quiet: bool = False):
        """`systemctl <args>` in the guest."""
        return self.ssh("systemctl", *args, capture=capture, check=check, quiet=quiet)

    def show(self, unit: str, *props: str) -> dict[str, str]:
        """Parse `show --property=` KEY=VALUE lines into a dict (no `--value`, which drops the keys)."""
        flags = [f"--property={p}" for p in props]
        out = self.systemctl("show", unit, *flags, capture=True, check=True) or ""
        result: dict[str, str] = {}
        for line in out.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                result[key] = value
        return result

    def is_system_running(self, quiet: bool = False) -> str:
        """`systemctl is-system-running`: e.g. `running`, `degraded`. Never raises."""
        return (self.systemctl("is-system-running", capture=True, check=False, quiet=quiet) or "").strip()

    def journal_combined(self, unit: str, cursor: str | None = None) -> tuple[str | None, str]:
        """The guest's `<unit>` journal and the kernel ring buffer, merged chronologically
        (`journalctl … _SYSTEMD_UNIT=<unit> + _TRANSPORT=kernel`), for live streaming a run.

        From this boot on the first call, then only entries past `cursor` (so a poll loop
        can print incrementally). Returns `(next_cursor, body)`: `body` is the new entries
        with journalctl's own `-- …` meta lines stripped; `next_cursor` resumes the next
        call (unchanged when nothing new). Never raises on a transient ssh failure.

        When the guest reboots mid-run the cursor's boot id is gone and journalctl prints
        "Failed to seek to cursor"; we retry once from `--boot` so the stream re-homes on
        the new boot instead of stalling, and never surface that error line as journal text.
        """
        def _query(selector: list[str]) -> str:
            args = ["journalctl", "--no-pager", "--output=short-precise", "--show-cursor"]
            args += selector + [f"_SYSTEMD_UNIT={unit}", "+", "_TRANSPORT=kernel"]
            return self.ssh(*args, check=False) or ""

        out = _query([f"--after-cursor={cursor}"] if cursor else ["--boot"])
        if cursor and "Failed to seek to cursor" in out:
            out = _query(["--boot"])
        next_cursor, body = cursor, []
        for line in out.splitlines():
            if line.startswith("-- cursor:"):
                next_cursor = line.split(":", 1)[1].strip()
            elif line.startswith("-- ") and line.endswith(" --"):
                continue
            elif "Failed to seek to cursor" in line:
                continue
            else:
                body.append(line)
        return next_cursor, "\n".join(body)

    def unit_exists(self, template: str) -> bool:
        """Whether the guest knows `<template>` (in-guest `list-unit-files`)."""
        out = self.systemctl("list-unit-files", template, "--no-legend",
                             capture=True, check=False) or ""
        return any(line.split() and line.split()[0] == template for line in out.splitlines())


def list_vms(filterText: str = "", **_: object) -> list[dict]:
    """`dynselect-list_vms` entrypoint: all guests, from their render sidecars.

    Globs `WORKERS_DIR/shared/vm/*.vars.json` (one per rendered guest, removed on
    destroy), the same source `f/qsu/bringup` lists for reuse. Pure stdlib so the
    dynselect runtime needs no extra deps; importing `f.qsu.common.vm_options`
    here would pull in jinja2, which the dynselect lock does not carry.
    """
    d = Path(os.environ["WORKERS_DIR"]) / "shared/vm"
    vms = sorted(p.name.removesuffix(".vars.json") for p in d.glob("*.vars.json")) if d.is_dir() else []
    return [{"label": v, "value": v} for v in vms if filterText.lower() in v.lower()]


# The xfstests group registry, relative to the xfstests store path (a guest's closure
# ships it once nixos-flake installs doc/; see the overlay's postInstall).
XFSTESTS_DOC_RELPATH = "lib/xfstests/doc/group-names.txt"

# Usable before any guest is up: a dropdown must never be empty/blocking.
_GROUPS_FALLBACK = [
    {"label": "auto: run automatically (~5 min cap)", "value": "auto"},
    {"label": "quick: under 30s each", "value": "quick"},
]


def parse_group_names(text: str) -> list[dict]:
    """Parse xfstests' `doc/group-names.txt` into `[{name, description}]`, in file order.

    The file is a fixed-width two-column table (`GroupName<whitespace>Description`) behind
    a 3-line `===`/`Group Name:`/`===` header. A line whose column 1 is a non-space token
    starts a new group; a line beginning with whitespace is a wrapped continuation of the
    previous group's description (joined single-spaced). The header rows are skipped (the
    `===` rules carry no column-1 name, the `Group Name:` label is dropped by name).
    """
    groups: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        # Skip blanks and the `===` rule rows (only `=` and inter-column spaces).
        if not stripped or set(stripped) <= {"=", " "}:
            continue
        if line[0].isspace():
            if groups:
                cont = line.strip()
                groups[-1]["description"] = (groups[-1]["description"] + " " + cont).strip()
            continue
        parts = line.split(None, 1)
        name = parts[0].strip()
        if name == "Group" and parts[1:] and parts[1].strip().startswith("Name:"):
            continue
        groups.append({"name": name, "description": parts[1].strip() if len(parts) > 1 else ""})
    return groups


def _group_options(groups: list[dict], filterText: str = "") -> list[dict]:
    """Turn parsed `[{name, description}]` into sorted, filtered dynselect options.

    `auto`/`quick` are pinned first (the common picks), the rest alphabetical. The label
    is `name: description` (description clipped to ~80 chars), the value the bare name.
    `filterText` matches case-insensitively against name + description.
    """
    needle = (filterText or "").lower()
    pin = {"auto": 0, "quick": 1}
    out = []
    for g in sorted(groups, key=lambda g: (pin.get(g["name"], 2), g["name"])):
        name, desc = g["name"], g.get("description", "")
        if needle and needle not in name.lower() and needle not in desc.lower():
            continue
        if desc:
            short = desc if len(desc) <= 80 else desc[:77].rstrip() + "..."
            label = f"{name}: {short}"
        else:
            label = name
        out.append({"label": label, "value": name})
    return out


def _guest_group_registry(vm_name: str) -> str | None:
    """The xfstests `group-names.txt` text from `vm_name`'s closure in the LOCAL nix
    store, or None. The dynselect runs on a default worker with no vsock to the guest
    but with /nix and the reuse sidecars mounted, so resolve it there: the sidecar's
    `closure.toplevel` -> the closure's requisites (`nix path-info --recursive`) -> the
    xfstests requisite that carries `XFSTESTS_DOC_RELPATH`."""
    if not vm_name or "/" in vm_name or vm_name in (".", ".."):
        return None
    sidecar = _workers() / "shared/vm" / f"{vm_name}.vars.json"
    if not sidecar.is_file():
        return None
    toplevel = (json.loads(sidecar.read_text()).get("closure") or {}).get("toplevel")
    if not toplevel:
        return None
    for line in Nix().capture("path-info", "--recursive", toplevel).splitlines():
        path = Path(line.strip())
        if "xfstests" in path.name:
            doc = path / XFSTESTS_DOC_RELPATH
            if doc.is_file():
                return doc.read_text()
    return None


def list_groups(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_groups` entrypoint: the xfstests group registry of a guest.

    Resolves `doc/group-names.txt` from the selected guest's closure in the local nix
    store (see `_guest_group_registry`) and parses it (`parse_group_names`); NOT over
    vsock-SSH, since a flow dynselect runs on a default worker that cannot reach the
    guest. Defensive by contract: a missing vm_name/sidecar/closure, or any nix/parse
    failure, returns a small `auto`/`quick` fallback rather than raising; a dropdown
    helper must never blow up the form (e.g. before a guest is booted).
    """
    try:
        text = _guest_group_registry((vm_name or "").strip())
        if text:
            opts = _group_options(parse_group_names(text), filterText)
            if opts:
                return opts
    except Exception:
        pass
    return _GROUPS_FALLBACK


def main():
    """Library module imported by the f/fstests/* steps; not a runnable step."""
    return "f/fstests/common: xfstests share config + xunit result helpers"
