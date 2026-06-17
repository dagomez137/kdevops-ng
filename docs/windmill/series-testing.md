# Testing a lore series: b4 + MAINTAINERS

Design for making a `b4`/lore patch series a first-class object across the build
and test flows, and for using the kernel's canonical `MAINTAINERS` data to
identify what a series touches and who to tell. The end goal is a tight loop:
ingest a lore series → build a kernel with it → identify the subsystem → boot →
run the matching fstests → report, and produce a review-ready `Tested-by:` reply.

Related: `kernel-build-flow`/`qemu-build-flow` (where `b4 shazam` already runs),
`qsu-execution-model.md` (boot), and `f/fstests/*` (the test run + report).

## What exists today

`b4` is already in the `nixos-flake` devShell, and a series is applied before a
build: `f/common/worktree.py` runs `b4 shazam <b4_series>` in the worktree, and
`b4_series` (a message-id/URL) threads through `f/kernel/prepare_worktree` →
`f/kernel/build` / `f/qemu/build` → `f/qsu/bringup`, even naming the worktree
slot. But nothing about the series is captured: only the post-apply `HEAD`. There
is no subject, version, changed-file list, or CC list, and the test report has no
idea which series (if any) produced the kernel under test.

`scripts/get_maintainer.pl` lives in the kernel worktree
(`<slot>/linux/scripts/get_maintainer.pl`); the fstests catalog is XFS-only today
(`FSTYP=xfs`, `f/fstests/common.py:xfs_catalog_text`).

## Decisions

- **Reporting is artifact-only.** The loop produces a review-ready reply
  (`In-Reply-To` the series, a `Tested-by:` trailer + the run summary) as an
  `.mbx` artifact on the share. The flow never posts to a public list — sending
  stays a manual, operator-reviewed step. (b4 has no "report to lore" command;
  the kernel convention is a reply email that maintainers later collect with
  `b4 trailers -u`.) Gated opt-in sending is a possible later addition.
- **MAINTAINERS is identify-and-flag, XFS only for now.** We resolve the touched
  subsystem(s), filesystem, and CC list, surface them, and flag when a series is
  not XFS/`fs`-generic — but we keep running the XFS catalog. Auto-selecting
  ext4/btrfs/f2fs sections needs catalogs that do not exist yet; deferred.
- **Build↔test bridge is a kernel-release-keyed sidecar, not flow threading.**
  `f/fstests/check` runs against a *booted guest* and is decoupled from the build
  (often a separate, later run). So the build writes `series.json` keyed by the
  built kernel release to a shared, vm-independent location; the report reads it
  back by the guest's `kernel_version`. No new cross-flow inputs.

## Stages

### 1. Series as a first-class object

In `f/common/worktree.py`, record the worktree `HEAD` *before* `b4 shazam`, then
after it derive a `series` object: `subject`, `version` (vN), `message_id`,
`base_commit`, `patch_count`, and `changed_files`
(`git diff --name-only <base>..HEAD`). Return it from `prepare_worktree`, fold it
into the kernel build manifest, and — when a series was applied — write
`<workers>/shared/kernel-series/<kernelrelease>.json` (the build knows its own
`make kernelrelease`). The fstests `report` reads that sidecar by the guest's
`kernel_version` and adds the series to the run-info table.

### 2. MAINTAINERS identification

New step (`f/series/maintainers`): in the kernel worktree devShell run
`get_maintainer.pl --no-git --subsystem -f <changed_files>` (and a CC variant) to
produce `{subsystems[], fstype, cc[], maintainers[], lists[]}`. Map the touched
paths to a filesystem (`fs/xfs/`→xfs, `fs/ext4/`→ext4, `fs/btrfs/`→btrfs,
`fs/f2fs/`→f2fs, other `fs/*`→generic/VFS). Persist alongside the series sidecar;
surface subsystem + fstype + CC in the report, and flag a non-XFS series against
the XFS harness.

### 3. Tested-by reply artifact

After a run, `f/fstests/report` (or a sibling) emits
`<share>/<vm>/<kver>/series-reply.mbx`: `In-Reply-To`/`References` the series
message-id, a `Tested-by: kdevops <…>` trailer, and the section/test summary plus
the result link. Never auto-sent.

### 4. "Test a lore series" flow

A top-level `f/series/test` that composes the existing flows: msgid →
`f/qsu/bringup` (build kernel with the series + boot) → `f/fstests/check`, then
surfaces the report + reply artifact. One entry point for "test this series".

## get_maintainer.pl recipe

Run from the kernel tree root. `--no-git --no-git-fallback` for speed/determinism
(F:/X:/N: matching only). `--subsystem` prints section names; default prints the
CC list (M/R/L). `-f <path>` matches a file path directly; a patch/mbox on stdin
matches by diff. Map subsystem label / `F:` path → FSTYP for the test decision.

## Risks

- Posting to public lists is outward-facing — hence artifact-only by default.
- `kernelrelease` at build time must equal the guest's `uname -r`; true when the
  closure boots the built kernel. A localversion mismatch just means the report
  shows no series (graceful), never a wrong one.
- Multi-FSTYP auto-selection is explicitly out of scope until ext4/btrfs catalogs
  exist.
