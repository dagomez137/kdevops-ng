# Plan: full blktests coverage in kdevops-ng

Working note, 2026-08-02. The mission: integrate blktests as the sixth
test suite, following `docs/contributing/test-suites.rst` (the law) and
the fstests worked example, with no wrapper around the suite: every UI
knob maps one-to-one to a `./check` option or a `config` variable that
blktests itself defines.

Upstream: `https://github.com/linux-blktests/blktests`, local expert
tree at `~/src/blktests` (fc6e3ffbd58c, 2026-08-02): 15 groups, 211
tests, `check` is 1285 lines of bash, no external runner.

## Current state (audited)

What exists today:

- `vendor/nixos-flake/modules/testSuites/blktests.nix` installs only
  runtime dependencies (nvme-cli, sg3_utils, multipath-tools, dmraid,
  lvm2, mdadm, targetcli-fb, fio, sysstat). No blktests package, no
  unit, no share, no state dir.
- `blktests` is a `test_suites` enum entry in the closure forms
  (`f/nix/render_config.py:33`, `render_config.script.yaml`,
  `f/nix/build.flow`, `f/qsu/bringup.flow`), so the module import
  already lands in the guest closure by default.
- Kernel fragments already cover: null_blk (+fault injection), loop,
  brd, nbd, scsi_debug, dm targets, md raid, zoned, NVMe core, NVMe
  fabrics incl. fcloop (enum-only, not in the default fragment list),
  blktrace, bcache (=m).

What is missing: the blktests package itself, the `blktests@` unit, the
share, the `f/blktests/` flow, source-override plumbing, fragments for
throtl/ublk/srp/rnbd, the docs page, fixtures, and the small patch
described below. nixpkgs has no blktests package (verified against
`~/src/nix/nixpkgs`), so the recipe is ours.

## Upstream facts that shape the design

- Invocation: `./check [options] [group-or-test...]`. Options are only
  `--device-only`, `--output=DIR`, `--quick[=SECS]`, `--exclude=X`
  (repeatable), `--config=FILE` (repeatable), `--cmd-trace`. Everything
  else is a `config` variable (a sourced bash file); precedence is
  command line > config file > environment > default. `config.example`
  is the canonical annotated knob list.
- No args runs every group except `meta`. Filters accept `group` or
  `group/nnn`. Tests named explicitly bypass EXCLUDE, DEVICE_ONLY and
  QUICK_RUN. EXCLUDE is exact-match only, no globs.
- Results: one TSV file per test at
  `$OUTPUT/<devdir>/<group>/<nnn>` (`status` pass/fail/"not run",
  `reason` output/exit/dmesg/kmemleak, `runtime`, `date`), plus
  `.full`, `.out.bad`, `.dmesg`, `.kmemleak` companions. `<devdir>` is
  `nodev` or the device basename, suffixed by the `set_conditions`
  variant (`nodev_tr_tcp_bd_file`), so one nvme test yields several
  result rows. The golden `.out` lives in the source tree, not results.
- `check` exits 1 only if a test failed; "not run" and dropped tests do
  not affect it. There is NO final summary, no total count, no
  machine-readable stream. Skip reasons go to stdout only; a failed
  `group_requires` prints one line, writes no file, and exits 0.
- Silent drops: device tests with empty `TEST_DEVS` produce neither
  output nor files; QUICK_RUN/DEVICE_ONLY/EXCLUDE filtering likewise.
- `TIMEOUT` is advisory: `check` never enforces it; only QUICK/TIMED
  tests honor it. Several tests default to 900 to 1200 seconds. A hung
  test hangs the run; there is no per-test scope, kill, or watchdog.
- Live progress signals that DO exist: a start line per test on stdout,
  and a `run blktests <test> at <ts>` marker written to `/dev/kmsg` at
  each test start. Under `StandardOutput=journal+console` both land in
  the journal our `wait` step already streams, so live per-test
  progress needs no patch.
- Each test runs in its own subshell; the test body is a sourced bash
  function, not a separate executable (this constrains the scope
  patch, see below).
- Root is required by every group except bcache, meta, rnbd. A
  `NORMAL_USER` account is needed by a few tests.
- srp/nbd/nvme-fabrics tests build their own targets (LIO, nbd-server,
  nvmet configfs); srp refuses to run if multipathd/srp_daemon are
  already active. `tlshd` (ktls-utils) is needed only by nvme TLS
  tests.

## The gap patch: per-test scopes and an enforced watchdog

fstests precedent: upstream xfstests `check` creates `fs<test>.scope`
itself and our overlay adds only RuntimeMaxSec driven by
`TEST_TIMEOUT`/`TEST_TIMEOUTS` (carried verbatim in
`vendor/nixos-flake/overlays/xfstests-runtime-max-sec.patch`).
blktests has no scope support at all, so our patch adds the whole
mechanism, kept minimal and shaped for upstream submission:

- At the top of the per-test subshell, when systemd is running and the
  feature-detect passes, move the subshell into a fresh transient scope
  named for the test (`blktests-<group>-<nnn>.scope`). Because the test
  body is an in-process function, `systemd-run --scope <cmd>` does not
  fit; the small implementation is a StartTransientUnit call (via
  `busctl call`) enrolling `$BASHPID`, with `RuntimeMaxSec` set from
  `TEST_TIMEOUT` (global) or `TEST_TIMEOUTS` ("group/nnn:secs ..."),
  exactly the fstests knob names. An alternative shape, re-executing
  the test under `systemd-run --scope`, is bigger and needs function
  export; prefer the busctl shape unless review says otherwise.
- Effect: `systemctl list-units --type=scope` names the in-flight
  test; a hung test dies surgically on the watchdog or by hand with
  `systemctl kill`; the parent loop sees the killed subshell and the
  flow records the test as failed (a started test with no result file
  is a kill/hang, never a pass).
- Carried as a patch in the vendored flake next to the package recipe,
  applied through the recipe's `patches` list, so a source-overridden
  tree still gets it (the override overlay replaces only `src`).
- Upstream candidates, in order of value: this scope/watchdog support;
  a final summary tally; a `status=running` stamp at test start. Mail
  to linux-block/linux-blktests once live-fired, then drop the carried
  copy on the version bump that includes it.

## Layer 1: kernel configuration (vendor/linux-config-fragments)

New fragments, each with its `builtin/` mirror, tristates `=m`:

- blk-cgroup/throttle: `BLK_CGROUP=y`, `BLK_DEV_THROTTLING=y`,
  `BLK_CGROUP_IOCOST=y` (throtl group and block iocost test). Decide
  in review whether this extends `storage/block-layer.config` (whose
  comment deliberately leaves some BLK_CGROUP knobs to `select`) or is
  a new `storage/blk-cgroup.config`.
- `storage/ublk.config`: `BLK_DEV_UBLK=m`.
- `storage/srp-target.config`: `INFINIBAND_SRP=m`, `INFINIBAND_SRPT=m`,
  `INFINIBAND_USER_MAD=m`, `INFINIBAND_IPOIB=m`, `TARGET_CORE=m`,
  `TCM_IBLOCK=m`, `DM_MULTIPATH=m` (+`_QL`/`_ST`), `DM_UEVENT=y`,
  `SCSI_DH_ALUA/EMC/RDAC=m` (srp group; RDMA_SIW/RXE already exist in
  nvme-fabrics.config).
- `storage/rnbd.config`: `BLK_DEV_RNBD_CLIENT=m`,
  `BLK_DEV_RNBD_SERVER=m`.
- bcache: add `BCACHE_DEBUG=y` where BCACHE lives (its rc checks it).
- Promote `storage/nvme-fabrics.config` from enum-only into the
  DEFAULT fragment list (`f/kernel/configure_fragments.script.yaml`
  defaults, mirrored in `f/kernel/build.flow` and bringup): the nvme
  group's default loop/tcp transports need it.
- `FAIL_IO_TIMEOUT=y` joins the existing fault-injection set in
  `storage/block-test-devices.config` (nvme/050, block).
- Optional, later: `DEBUG_KMEMLEAK` as a debug fragment (blktests
  auto-enables kmemleak checking when present).

Run `verify_config.sh` on a merged config before committing, per that
project's rules. Kernel rebuild required to live-validate.

## Layer 2: guest closure (vendor/nixos-flake)

New package `pkgs/blktests.nix` (callPackage, registered in
`pkgs/default.nix` and the flake `packages` output, exactly the
libbpf-tools precedent):

- `src = fetchFromGitHub` pinned to a recent tag/rev; `make` builds the
  `src/` C helpers (buildInputs: liburing for the uring helpers; the
  in-tree miniublk builds against kernel headers >= 6.4);
  `make prefix=$out install` lands `check`, `tests/`, `common/`,
  `src/` under `$out/blktests`. No wrapper binary: the unit sets the
  working directory instead. `patches = [ ./blktests-scope.patch ]`.
- Since this is our own mkDerivation there is no nixpkgs
  patchPhase-replacement gotcha (that trap is specific to the nixpkgs
  xfstests recipe).

Extend `modules/testSuites/blktests.nix` (mirror `fstests.nix`):

- `stateDir = "/var/lib/blktests"`; tmpfiles `d` rule for it.
- `environment.systemPackages`: add `pkgs.blktests` plus the probed
  tools the module does not yet carry: nbd, blktrace, xfsprogs
  (xfs_io), parted, e2fsprogs, f2fs-tools, btrfs-progs, dosfstools,
  cryptsetup, util-linux, gawk, bc, expect, keyutils; ktls-utils
  (tlshd) when the TLS tests join. Keep the module self-contained even
  where devel overlaps.
- A `blktests` unprivileged account for `NORMAL_USER` tests.
- The unit, on the `xfstests@` shape:

      systemd.services."blktests@" = {
        description = "blktests check (group %i)";
        path = [ "/run/current-system/sw" ];
        serviceConfig = {
          Type = "oneshot";                # no RemainAfterExit
          WorkingDirectory = "${pkgs.blktests}/blktests";
          EnvironmentFile = "-/var/lib/blktests/%i.env";
          ExecStart = "${pkgs.blktests}/blktests/check --config=/var/lib/blktests/config --output=/var/lib/blktests/%v/results $BLKTESTS_ARGS";
          StandardOutput = "journal+console";
          StandardError = "journal+console";
          TimeoutStartSec = "infinity";    # the flow's wait owns the deadline
          SyslogIdentifier = "blktests";
          StartLimitIntervalSec = 0;
          Documentation = "https://github.com/linux-blktests/blktests";
        };
      };

  `%i` is the group (names the instance and keys the env file), `%v`
  keys results by kernel release as fstests does. `$BLKTESTS_ARGS`
  carries only positionals (the group, or an explicit test list); all
  tunables live in the rendered `config`, which sidesteps the getopt
  optional-argument trap on `--quick` entirely. The source tree in the
  store stays read-only: with `--config` and `--output` pointed at the
  share, `check` writes nothing else (its tmpdir lives under OUTPUT).
- Register nothing new in the vendored flake (the module is already in
  `nixosModules.testSuites` and the `imageless-blktests` check).

Source override (patches still applied):

- Add `"blktests"` to `_OVERRIDABLE_PKGS` in `f/nix/render_config.py`
  and to the curated `source_overrides` form (render_config sidecar,
  `f/nix/build.flow`, bringup via the generator). The generated
  `lib.mkAfter` overlay does `prev.blktests.overrideAttrs (_: { src =
  inputs.blktests-src; })` on top of the composed default overlay, so
  the carried patch and build inputs survive a custom tree, and
  `lock_config` re-locks `blktests-src` to branch tip on every build.
  No `_PKG_SOURCE_ATTRS` entry needed (make-only build, no configure).

Share: `/var/lib/blktests`, tag `blktests`, host side
`$WORKERS_DIR/shared/blktests/<vm>/`. Contract:

    <share>/config              rendered blktests config (bash)
    <share>/<group>.env         BLKTESTS_ARGS for instance %i
    <share>/<kver>/results/...  OUTPUT tree (TSV files + companions)
    <share>/<kver>/report.json  report rollup

## Layer 3: the flow (f/blktests/)

`f/blktests/check.flow` composing verb steps, all on the fstests
chassis (vsock-SSH remote, devshell runners, vm/vm-run tag split):

1. `discover` (vm): gate `is-system-running`, the `blktests@` template,
   `check` executable; enumerate devices and the installed group
   catalog (`tests/*/rc` under the package path); capture
   `uname -r`; refuse an empty enumeration; write the per-VM cache the
   pickers read.
2. `render_config` (vm): render `config` from the curated knobs (only
   set what the user set; `TEST_DEVS=( ... )` array syntax), write
   per-group `.env` files, optionally clean results, prune stale share
   entries. When the gated raw-config override is set it replaces the
   curated rendering wholesale, fstests-style.
3. Sequential `forloopflow` over selected groups, `skip_failures:
   true` (per the RST; note fstests still carries `false`):
   `wipe` (vm, only when TEST_DEVS were chosen and wipe_devices is on)
   -> `start` (vm) -> `wait` (vm-run) -> `collect` (vm).
   - `start`: run identity first: remove `<kver>/results/*/<group>`
     subtrees so anything present afterwards is this run's; then
     `systemctl start --no-block blktests@<group>`.
   - `wait`: poll unit properties to terminal state, host
     `qemu-system@<vm>` liveness as the death authority, stream the
     merged unit+kernel journal (the kmsg `run blktests <test>`
     markers make the job log a live per-test view), stop on deadline.
   - `collect`: walk the group's result TSVs; a group is `passed` only
     if the unit finished, at least one result file exists, no file
     says `fail`, and neither `crashed` nor `timed_out` is set.
     Zero files is `notrun` and never a pass (this catches the
     upstream group_requires exit-0 vacuous pass). Scrape skip reasons
     and the group_requires line from the journal as row annotations
     (they exist nowhere else). Rows carry group, devdir/condition
     variant, test, status, reason, runtime, failures first.
4. `report` (vm): `render_all` as the sole key (run info, one row per
   group, one row per test), atomic `report.json` write.
5. `annotate` (vm): `f/monitoring/annotate` with suite `blktests`.
6. `judge` (vm): shared `run_status` in `f/blktests/common.py`.
7. `failure_module` `stop` (vm): stop + reset-failed every selected
   `blktests@<group>`, and stop lingering `blktests-*.scope` units
   (scope processes live outside the service cgroup, so a unit stop
   alone can orphan a scoped test); idempotent, never fails.

`common.py` holds: share paths (kver-keyed, traversal-hardened), the
static curated group catalog (15 groups, descriptions from each rc,
test counts), the config renderer, `build_check_args` (positional
list; enforce the groups-versus-tests exclusivity `check` does not),
the seqres TSV parser, the results-tree walker, `run_status`.
`selfcheck.py` arms `selfcheck.check("blktests", "per_group",
"group")`.

## The UI: one-to-one knobs

Knob names are blktests' own keywords; schema `title:` fixes casing
(NVMe, RDMA, TCP). No invented abstractions.

| Form knob | blktests interface |
|---|---|
| `vm_name` | (ours) dynselect-list_vms |
| `test_selection` groups\|tests | positional args |
| `groups` | positional group list; dynmultiselect from discover cache, static catalog fallback; default = all except `meta` (upstream default) |
| `tests` | positional `group/nnn` list |
| `exclude` | `EXCLUDE` (exact group or group/nnn; note no globs) |
| `test_devs` | `TEST_DEVS` (dynmultiselect from discovered devices; default empty = nodev run; destructive, pairs with `wipe_devices`) |
| `device_only` | `DEVICE_ONLY` |
| `quick_run` | `QUICK_RUN` |
| `timeout` | `TIMEOUT` (advisory upstream; enforced only via our watchdog knobs) |
| `run_zoned_tests` | `RUN_ZONED_TESTS` |
| `normal_user` | `NORMAL_USER` (default: the module's `blktests` user) |
| `nvmet_trtypes` | `NVMET_TRTYPES` multiselect loop/tcp/rdma/fc (default loop) |
| `nvmet_blkdev_types` | `NVMET_BLKDEV_TYPES` multiselect device/file |
| `nvme_img_size` | `NVME_IMG_SIZE` |
| `nvme_num_iter` | `NVME_NUM_ITER` |
| `use_rxe` | `USE_RXE` (off = siw; rnbd requires on) |
| `throtl_blkdev_types` | `THROTL_BLKDEV_TYPES` |
| `edit_config` + `config` | gated raw config replacing the form |
| Service group | `timeout` 86400, `poll_interval` 15, `stream_logs`, `test_timeout`/`test_timeouts` (the patch's TEST_TIMEOUT/TEST_TIMEOUTS), `reboot_timeout` |

Advanced/deferred knobs: `TEST_CASE_DEV_ARRAY` (md/003, bcache, zbd
arrays; an advanced object knob or raw-config-only at first),
`NVME_TARGET_CONTROL`, `KERNELSRC` (nvme/056 needs a kernel source
tree on the guest; excluded with reason).

## Registration and wiring checklist

- `f/nix/render_config.py`: share auto-add
  (`"blktests" in test_suites` -> `/var/lib/blktests`, tag
  `blktests`); `_OVERRIDABLE_PKGS` += blktests.
- `f/qsu/common.py`: `CANONICAL_SHARE_TAGS` += `blktests`; `_shares()`
  branch for `$WORKERS_DIR/shared/blktests/<vm>`.
- `f/qsu/bringup.flow` flags expression: add the blktests include line
  (via `scripts/gen-bringup.py` where generated; the source_overrides
  and closure schema changes flow through the generator, then
  `python3 scripts/gen-bringup.py` and the `generated` check).
- `nix/apps/default.nix`: new `f/blktests/**` objects join the
  `stagingOnlyPrune` list until promoted (ADR 0012).
- Deploy mechanics: after any `vendor/` edit run
  `nix run .#windmill-install`; publish `f/**` with
  `nix run .#deploy-staging` (never a bare push to kdevops); promotion
  is deleting the prune lines.

## Tests and gates

- `tests/test_blktests_common.py`: seqres TSV parse (pass/fail/notrun,
  reason, runtime, custom keys), missing/truncated file degradation,
  results-tree walk with devdir/condition fan-out, config renderer
  (array syntax, only-set-what-is-set, raw override verbatim),
  positional builder exclusivity, `run_status` refuses vacuous runs,
  path traversal guards, catalog ordering.
- `scripts/preview-smoke.py` SUITES += `("blktests",
  "f/blktests/check.flow", "per_group", "group")`.
- Vendored gates: `nix flake check` in `vendor/nixos-flake` (the
  imageless-blktests check now pulls the package + unit),
  `verify_config.sh` in the fragments project; repo gates
  `nix flake check` + `scripts/check-style.sh`.
- Commit series is atomic per tree and layer, each vendored project
  under its own message rules (50-char subjects in nixos-flake).

## Layer 4: documentation

`docs/flows/blktests.rst`, staged `:orphan:` in `docs/staging.rst`,
UI-first, mirroring `docs/flows/fstests.rst`'s heading arc: run form;
devices (nodev groups create their own null_blk/scsi_debug/nvme-loop
devices; `TEST_DEVS` runs are destructive); blktests owns device
setup; service units to query (`blktests@<group>.service`,
`blktests-<group>-<nnn>.scope`); querying status and logs; where the
run lives on the guest (the share contract); running a group by hand;
restarting a hung test (watchdog knobs). Shared guest content links to
`docs/flows/guests.rst`. `cmd_links` additions: `blktests`,
`nbd-server`, `blkzone`, `tlshd` as needed; `:src:` on our unit and
flow sources. Update `docs/roadmap.rst` (blktests moves out of
planned) on promotion.

## Phasing to full coverage

Phase 0, guest foundation: package + patch, module extension (unit,
share, state dir, user, tools), share wiring in f/nix + f/qsu,
fragments for throtl/ublk + nvme-fabrics into defaults, kernel + VM
rebuild. Exit: `systemctl --host <vm> start blktests@loop` by hand is
green with results on the share.

Phase 1, flow MVP on device-free groups: full step chain + docs draft;
live-validate loop, nbd, block (nodev), throtl, blktrace, ublk, nvme
on loop+tcp transports; negative paths (hung test bounded by wait,
group-requires skip is a red judge, killed VM is `crashed`). Exit: the
RST's definition of done for these groups, tables and journal
streaming verified.

Phase 2, device runs: `TEST_DEVS` picker + wipe on the VM's spare NVMe
disks, `DEVICE_ONLY`, zbd (zoned null_blk fallback plus a real zoned
null_blk TEST_DEV), scsi via a scsi_debug or virtio-scsi TEST_DEV
(the SCSI target stack is already enabled in the imageless kernel),
dm, md (single-dev tests), meta as an opt-in integration selfcheck.

Phase 3, the long tail: `TEST_CASE_DEV_ARRAY` (md/003, bcache with
bcache-tools + BCACHE_DEBUG, zbd arrays), srp (fragment + multipathd
constraints), rnbd (fragment + USE_RXE), nvme rdma + fc transports,
nvme auth/TLS (tlshd service), watchdog patch live-fire and upstream
submission, rublk as an alternative `UBLK_PROG`.

Phase 4, promotion: fixture/preview/selfcheck complete, docs page out
of staging, prune lines deleted (staging -> kdevops), roadmap updated,
memory + handoff notes refreshed.

## Progress log

Append one entry per phase with the conclusion and results.

### Phase 0: guest foundation (2026-08-02, DONE)

Landed as eight atomic commits (99e2004..9224643 on main), pushed to
the staging workspace, workers' vendor copy re-synced.

- Package: `pkgs/blktests.nix` pinned to upstream fc6e3ffbd58c;
  builds all src/ helpers including miniublk and the liburing pair.
  Found and fixed an upstream install-layout defect: `make install`
  flattens the sg/ helpers into src/, where scsi/001 and scsi/002
  resolve `src/sg/<name>` and silently skip; postInstall restores
  the layout. Upstream fix candidate.
- Scope patch: `pkgs/blktests-runtime-max-sec.patch`, validated live
  on the dev host before shipping. Two findings from probing:
  StartTransientUnit only queues the cgroup migration, so children
  forked before membership escape the scope (the patch polls
  `/proc/$BASHPID/cgroup`); and bash delivers the deferred TERM trap
  inside the active output redirection, so the watchdog message
  lands in the test's `.out` and thus in the failure diff, which
  check echoes to stdout and the journal. End-to-end: a synthetic
  hanging test died at 2.17 s of a 2 s deadline, recorded as a fail
  row with `reason output`, and the following test ran and passed.
  A timed-out scope stays `failed` until reset; the patch
  reset-fails before start and the flow's stop step will too.
- Module: `blktests@` renders exactly to spec in the composed
  imageless closure (checked the generated unit file); vendored
  `nix flake check` green.
- Fragments: cgroup IO controllers into block-layer, ublk pair,
  FAIL_IO_TIMEOUT, all mirrored into `imageless_defconfig` in
  savedefconfig order. `verify_config.sh` 25/25 on a full
  default-list merge; a baseline diff proved the pre-existing
  mismatches on the v6.19 host tree are unchanged by this work.
- Wiring: share end to end, source override (patches survive),
  nvme-fabrics and ublk into the default fragment set, bringup
  regenerated, 489 fixture tests green, all repo gates green.
- Operational gotcha recorded: never reference this repo root as a
  `path:` flake; it copies the workbench kernel worktrees into the
  Nix store (three 9.9 GiB copies filled the disk; deleted, 27.8
  GiB freed). Use the git-based `.#` refs.

### Phase 1: flow MVP, live-validated (2026-08-03, DONE)

The `f/blktests/check` flow is live in the staging workspace and
validated end to end on VM `blktests-check` across three kernel
rebuilds (the preset gained NVMe fabrics, then the block test
drivers, each derived via the savedefconfig round-trip). Evidence,
all driven through Windmill runs:

- By hand first (the Phase 0 exit criterion): `blktests@loop` on the
  armed share ran 13 tests, 11 pass and 2 honest reds, with
  `blktests-loop-006.scope` naming the in-flight test mid-run (the
  scope patch live on a guest).
- Red path through the flow: the loop group run produced
  failures-first rows with runtimes, a red judge, and the failure
  module stopping the unit.
- Green path: the blktrace group passed through the full chain
  (rows, tables, green-to-judge), blktrace/002 an honest notrun.
- The quick-run sweep (14 groups selected; cancelled at the throtl
  guest OOM, see findings) banked six groups from the share:
  block 25/1/3 (pass/fail/notrun), nvme 42/0/6, srp all-notrun
  (kernel then lacked the modules), zbd 4/5/2, loop 10/2/0,
  scsi 5/0/1. The nvme number is the fabrics defconfig work paying
  off: the whole loop-transport matrix runs green.

Live-run fixes folded back in: discover probes the unit template
through a throwaway instance (`systemctl show` refuses a bare
template name), and start's ActiveState assertion is gone (a
sub-second all-skip group finishes before the read-back; the
verdict belongs to wait and the result files). Both were found by
the flow's own first runs, exactly what staging is for.

Findings ledger (honest reds and environment bugs, none papered
over):

- loop/010 and loop/013: `systemctl mask systemd-udevd` refuses on
  NixOS because the /etc unit path is already a store symlink; the
  mask noise fails the output diff, and 013's partition-reuse check
  fails downstream of the unmasked udevd. Guest-module or upstream
  fix candidate.
- nbd group: nixpkgs nbd 3.27.1's `nbd-client --help` segfaults on
  every help spelling (host and guest, reproduced on the shared
  store binary), so blktests' `-L` capability probe sees nothing
  and the group skips. nixpkgs/upstream bug candidate; overlay fix
  or version bump is the Phase 3 path.
- throtl/005 OOMs a 4 GiB tmpfs-root guest within ~25 s (writeback
  throttling accumulates dirty pages; the OOM killer took an sshd
  session and the vsock transport with it). The rerun boots with
  8 GiB and arms the scope watchdog; if it recurs at 8 GiB it is an
  upstream finding.
- Flow hardening gap exposed by that OOM: with the host
  `qemu-system@` unit still active, a transport-dead guest makes
  `wait` retry SSH failures until the flow deadline; the run needed
  an operator cancel. Same semantics as fstests today; a bounded
  transport-failure budget is a candidate hardening.
- block/046 failed and zbd went 4/5/2 in quick mode; both queued
  for triage on the next pass (the zbd cluster may be the advisory
  30 s budget biting, which the armed watchdog run will tell).

### Phase 2: device runs, watchdog sweep, curated pickers (2026-08-03, DONE)

On the srp/rnbd/bcache kernel with an 8 GiB guest, all driven and
verified through Windmill runs in staging:

- The remaining-groups sweep with the scope watchdog armed: throtl
  14/0/0 (the 4 GiB OOM does not recur at 8 GiB, so it is guest
  sizing guidance, not an upstream bug), ublk 6/0/0 on the new
  driver, blktrace passing; honest reds for nbd (userland bug), md
  (md/001 now a real failure to triage), rnbd (both notrun: the run
  must arm USE_RXE=1), and bcache/srp whole-group skips (the device
  array knob gap; srp's group_requires reason still to fetch).
- The first destructive TEST_DEVS run (two NVMe disks, wiped):
  block ran 37 green across nodev plus both devices with per-device
  rows, dm's device tests passed on them, zbd's fallback zoned
  null_blk appears as its own devdir with a stable 005/006/011
  failure cluster to triage, and scsi went all-notrun under
  TEST_DEVS (a scsi_debug leftover interaction, queued).
- Curated pickers now cover every list knob: TEST_DEVS is a device
  dropdown labeled with sizes, Tests picks from the guest's 211
  installed tests, EXCLUDE offers groups then tests; discover
  caches all three enumerations together and the pre-discovery
  fallbacks are the static catalog snapshot and the canonical qsu
  disk paths. Validated live: a tests-mode run through the new list
  input executed exactly the picked tests per group instance, and
  the deployed pickers read the fresh cache (15 groups, 211 tests,
  5 devices at 20G).

### Phase 3 retest and promotion (2026-08-03, DONE)

The fix batch validated through Windmill runs on the rebuilt stack:
md 3/0/0 (the udev-rules fix), rnbd 2/0/0 (USE_RXE armed), block
fully green including 046, throtl and ublk already clean, and the
srp-first retest after the configs-module autoload: srp 13/0/2 (the
whole SRP multipath-over-soft-RDMA story runs), zbd 8/1/2 with
mq-deadline clearing 005 and 006, scsi 5/0/1. Two more root causes
landed on the way: /proc/config.gz needs the configs module loaded
(IKCONFIG=m), now auto-loaded by the suite module, and srp must run
before the groups that load scsi_debug in the same boot.

Remaining honest findings, each documented above: zbd/011 (dm-crypt
over zoned, single-test failure, upstream candidate), the nbd-client
help segfault, the loop udevd-mask pair, scsi/007 (failed once under
a dirty module state, green when clean; watch), and the deferred
TEST_CASE_DEV_ARRAY knob with the nvme rdma/fc/TLS transport
extensions. With the executor validated end to end and every red
diagnosed, the suite is promoted to the kdevops workspace by
deleting its staging-only prune entry.

### Phase 3: triage queue (2026-08-03, diagnoses)

Diagnoses from the banked artifacts and the guest journal, each with
its fix or disposition:

- md/001: mdadm's create times out waiting for the /dev/md symlink
  its udev rules make; the rules never reached the guest's ruleset
  (systemPackages does not install rules). FIXED: the module
  registers mdadm with udev.
- zbd/005 and 006: `echo deadline > .../queue/scheduler` fails with
  EINVAL because the preset disabled MQ_IOSCHED_DEADLINE, the
  scheduler that serializes zoned writes. FIXED: the preset accepts
  the Kconfig default (y). zbd/011 (dm-crypt over zoned) retests
  after this.
- srp whole-group skip and the scsi all-notrun: one root cause,
  "scsi_debug is already loaded before test", a leftover module
  from earlier groups in the same boot. The reboot each bringup
  performs clears it; a run-hygiene knob (unload or reboot before
  the loop) is an open design choice. The nvme disks are correctly
  rejected as non-SCSI test devices; real scsi device tests need a
  SCSI-attached disk (open: a virtio-scsi drive in bringup).
- Transport-death gap: FIXED in wait, twenty consecutive failed
  polls with qemu alive now end the group as crashed instead of
  retrying to the deadline.
- rnbd: retesting with USE_RXE=1 armed.
- block/046: its failure diff was consumed by a later run's
  identity wipe; rerunning to recapture.
- Open, deferred with reasons: the nbd-client --help segfault
  (nixpkgs nbd 3.27.1; overlay fix or bump), the loop/010 and 013
  udevd-mask refusal on NixOS (upstream-facing discussion), the
  TEST_CASE_DEV_ARRAY advanced knob for bcache and md/003.

### Final results (2026-08-03, PROMOTED)

blktests is the sixth suite, live in the ``kdevops`` workspace
(`f/blktests/check` deployed by the pruned promote push and verified
there with a green tests-mode run). The integration spans all four
layers: the package with its carried per-test scope/watchdog patch,
the ``blktests@<group>.service`` executor module with its share, the
thin check flow with curated pickers on every list knob, and the
staged docs page. The kernel surface (fragments and the imageless
preset, kept in lockstep via the savedefconfig round-trip) now
covers every group: fabrics, block test drivers, cgroup IO
controllers, ublk, srp, rnbd, bcache debug, and mq-deadline.

Best validated coverage per group on v7.1-rc7 (quick mode unless
noted): block 37/0 with per-device rows (destructive TEST_DEVS run),
nvme 42/0 on the loop transport, srp 13/0, throtl 14/0 at 8 GiB,
zbd 8/1 (only zbd/011 red), ublk 6/0, scsi 5/0, md 3/0, rnbd 2/0,
blktrace 1/0, loop 11/2, dm device tests green. Honest reds and
skips, all diagnosed: zbd/011 (dm-crypt over zoned, upstream
candidate), nbd (nbd-client help segfault in nixpkgs 3.27.1), the
loop udevd-mask pair on NixOS, scsi/007 under a dirty module state,
and bcache plus md/003 pending the TEST_CASE_DEV_ARRAY knob.

Queued upstream candidates: the scope/watchdog patch for check, the
sg/ install-layout fix, the nbd-client segfault report, and zbd/011
once reproduced against mainline. Deferred features: the device
array knob, nvme rdma/fc/TLS transport runs, and a run-hygiene
(reboot or module-unload) knob for back-to-back groups on one boot.
The local commit series is not yet pushed to the bare repository,
and the docs page stays a staged orphan until reviewed.

## Honest exclusions (recorded, not silent)

- `nvme/056`: needs `KERNELSRC`, the ynl CLI, and `CONFIG_ULP_DDP`
  (not in mainline); excluded with reason.
- `meta` group stays out of the default set (upstream's own default);
  it runs on request and in integration selfchecks.
- Tests that skip for missing hardware (real SCSI, large NVMe) stay
  visible as `not run` rows with journal-scraped reasons, never
  dropped from the report.
