# Handoff: add a new test suite (session context, 2026-07-04)

Uncommitted working note for the next session. The mission there: integrate a
new test suite. **Start by reading `docs/contributing/test-suites.rst`**: it
is the canonical requirements document, distilled from the two worked
examples (fstests, kunit), with every rule linked to the code that defines
it. This note carries the session state the RST does not.

## UPDATE 2026-07-04 (later the same day): kselftests SHIPPED

The third suite landed: kernel selftests (`f/selftests/run`, suite name
`selftests`, units `kselftest@`/`kselftest-test@`). Nine commits
`b9e376d..323a8d6` on `main` on top of `199bb6d`; **the push to the local
bare is PENDING** (permission classifier blocked `git push origin main`; it
is a fast-forward, push by hand). Live-validated end to end on VM
`selftests-check`, kernel `7.2.0-rc1-vanilla-80763834955b`: 26-collection
default run (19 green, honest reds), module tests proven (`sysctl`+`lib`
pass via the `/sbin/modprobe` shim; `kmod` passes at
`override_timeout=360`, measured 4m00s against the upstream 45 s default),
negative path (`seccomp` at 1 s = `# TIMEOUT` rows + red judge), manual
`systemctl --host <vm> start kselftest@size` + journal KTAP, and both hang
classes (`pidfd`, `proc-maps-race`) bounded by `wait`'s deadline with the
loop continuing. New generic rules were folded into the requirements RST
(journald-socket stdout, FHS tmpfiles shims). Follow-up the same evening: the module-init family became the FOURTH
suite, `runtime-tests` (`f/runtime_tests`, docs/flows/runtime-tests.rst;
series now 15 commits `b9e376d..HEAD`). Full catalog of 13 modules
LIVE-VALIDATED green on kernel `da00af33839a`, including the inverted
`test_ida` and the -EAGAIN auto-unload class. THE FINDING that reshaped
its verdict layer: under `modprobe@.service` the module's exit status is
structurally unobservable (the ExecStart `-` prefix makes every exit
"expected", and expected exits log only at debug, which PID1 never
journals), so the verdict rides kmsg evidence (per-module summary or
sentinel regex, all source-verified) plus the post-run
`/sys/module/<mod>` load state per class. Fresh-run identity proven (an
already-loaded module would condition-skip; start unloads first). See
the `runtime-tests-suite` memory. Remaining suite-integration gaps in
this family: none cataloged (LKDTM/test_parman/test_dhry excluded with
reasons).

2026-07-05: firmware collection added = ALL SEVEN legacy selftests
sections now covered, no gaps (5 commits 0649108..76ba5be). Small
kselftest-collection extension: 6 config symbols in the fragment+preset,
one tmpfiles `d /lib/firmware` in selftests.nix (the fw_namespace helper
mounts a tmpfs there without mkdir and runs first), firmware in the
curated TARGETS+catalog. The c-developer dive corrected the legacy Debian
prep: configfs is NOT a firmware prereq (confused with IKCONFIG's
`configs` module) and there is no udev firmware rule to remove on NixOS
(systemd dropped it in 2014). Live: runs end to end, all sub-tests pass
EXCEPT fw_upload.sh's final fw1 readback compare, which fails
deterministically while the upload works in isolation = a candidate
upstream firmware_loader/test finding on the v7.2-rc1 clang+debug kernel.

2026-07-05: legacy-kdevops modules-testing coverage closed. The old
project's `module` section (SELFTESTS_SECTION_MODULE) = the kselftest
`module` collection (find_symbol.sh kallsyms stress under perf), which
was not in our curated set; added the TEST_KALLSYMS family to the preset
and `module` to the curated TARGETS + catalog, live green (0.68 s, no
timeout bump). ALL SEVEN legacy selftests sections now covered (kmod,
sysctl, module, xarray, maple, vma) EXCEPT firmware, the one remaining
recorded gap (TEST_FIRMWARE + selftests.nix guest prep: drop the
50-firmware.rules udev rule, mkdir /lib/firmware, preload configfs).

2026-07-05: the FIFTH suite landed, `usertests` (kernel-tree userspace
harnesses: radix-tree/vma/memblock via `usertests@` units; f/usertests
+ f/kernel build_usertests/publish_usertests + build-usertests devShell
+ testSuites/usertests.nix; zero kernel-config surface). Live: 6/7 green
(vma 27/27, xarray 159.6M, maple 421.8M, idr-test 94M, multiorder,
memblock 181) + ONE HONEST RED that is a REAL FINDING: gating UBSan
(halt_on_error=1, pinned in the unit) caught a shift-exponent-107 UB at
lib/xarray.c:437 in the path only radix-tree/main exercises; upstream
report candidate, reproducer `UBSAN_OPTIONS=halt_on_error=1 ./main -s
1783227741`. Excluded as upstream-broken at v7.2-rc1 (both fix
candidates): the tools/testing/rbtree pair (missing kmalloc_objs in the
shared shim) and tools/testing/scatterlist (header predates
folio/page_range_contiguous). Full chronology: the `usertests-suite`
memory.

Late additions the day before: all three journal-driven suites now report
a numeric `time(s)` column per item plus a run total (canonical
extraction: monotonic delta between the run's SD_MESSAGE_UNIT_STARTING
and its closing job record; fstests keeps its xunit times), and
`update_lock` defaults true after the per-worker flake.lock gotcha (each
worker's per-VM config dir kept its own stale lock and silently rebuilt
an old vendored flake; a guest booted a two-generations-old closure
while the build reported success). The old-kdevops review
(refactor/kdevops selftests role) confirmed parity on kmod timeout
handling and sysctl tuning, and identified the remaining UNCOVERED
family for a future suite: the kernel-tree userspace harnesses
(tools/testing/radix-tree xarray+maple userspace binaries,
tools/testing/vma), plus the firmware collection's guest prep (remove
the 50-firmware udev rule, create /lib/firmware, preload configfs) when
firmware joins the curated set. Open triage: pidfd hang,
proc-maps-race crash-loop (possibly a real v7.2-rc1 catch), the honest
content failures (step_after_suspend needs CONFIG_SUSPEND, mount_setattr,
ptrace vmaccess, cgroup kmem/memcontrol, exec non-regular); upstream patch
candidates: a `settings` file for tools/testing/selftests/kmod, the
runner's `/dev/stdout` reopen under socket stdout.

## Where main stands

Head `199bb6d` on `main`, force-pushed to the local bare (`origin` =
`~/.git-bare/kdevops-ng.git`). GitHub `upstream` is far behind at `336b37a`;
everything since is local-only and was history-rewritten several times, so
it can be reshaped again until pushed to GitHub. Backup tags exist
(`backup/2026-07-03-pre-kunit-rewrite`, `backup/2026-07-02-1a92c45`).

The local-only series (bottom-up): the vsock-SSH transport promotion, the
KUnit config fragments (`test/kunit.config` on-demand + `kunit-autorun` +
`builtin/` mirror; the preset builds `KUNIT_ALL_TESTS=m`, autorun off), the
kunit executor module (`kunit@` strict / `kunit-results@` read-back /
`kunit-test-modules` boot scan), the closure registration, the
`f/kunit/run` flow, the curated `kernel_parameters` boot toggle
(`kunit.autorun=1`), the KUnit docs page, toolchain/flake fixes, the kunit
Test Suites form default, five fstests forward fixes (stale `result.xml`,
clean-stop, fd leak, stop logging, judge), the Windmill fork pin bump, the
test-suite requirements page, the xfstests watchdog overlay, and the
fstests scope-story correction.

## What is DONE and live-validated

- KUnit end to end on this host (hz-debian): kernel
  `7.2.0-rc1-vanilla-6415616f2381` (new preset), VM `kunit-check`, 68
  modules declared by the boot scan, 111 suites registered, a 4-suite
  builtin+module run green with judge, rich `render_all` tables, kver-keyed
  `report.json`, and the wait step's streamed KTAP retrievable (105 KB via
  the fork's new disk log storage).
- The Windmill OSS log-drop bug (any >9000-char capture batch silently
  discarded) fixed in the fork (`6c36e7e7c1` on `integration/fixes`),
  pinned and deployed.
- The audit trail: `notes/kunit/full-support-audit.md` (uncommitted) has a
  Status section mapping every finding to fixed/superseded/open.

## What is PENDING

1. **Fork push**: `~/src/windmill-labs/windmill` `integration/fixes`
   (`6c36e7e7c1`) is only on the local bare; push to
   `github.com/dagomez137/windmill` so the pin resolves off-host. The store
   here is pre-seeded (`nix store add-path --name source`), so local builds
   work regardless.
2. **xfstests watchdog live-fire**: the overlay now carries the author's
   own patch verbatim (`overlays/xfstests-runtime-max-sec.patch`, origin
   `~/src/xfstests-dev` commit `509c3cdd` on `iomap-buf-writethrough2`,
   queued for upstream). Build-verified only; needs a rebuilt fstests guest
   plus a section run with a small Per-test Timeout. Consider mailing the
   patch to fstests upstream; drop the overlay copy on the bump that
   includes it.
3. `rust_rxarray` runs need a kernel built from the rxarray dev branch
   (vanilla trees do not carry that suite).
4. Deliberately-open kunit items (audit doc): attribute/speed surfacing in
   tables, a repeat/iterations knob, extra curated `kernel_parameters`
   entries.
5. Uncommitted review drafts stay uncommitted on purpose:
   `notes/kunit/full-support-audit.md`,
   `notes/fstests/`-adjacent `docs/fstests/xfs-profile-coverage.md` (check
   `git status`), and this note.

## Environment state

- Windmill stack up (`systemctl --user`: db, server, caddy, native, extra,
  workers 0000-0004; 0002=vm, 0003/0004=vm-run tags). UI via
  `ssh -L 8000:localhost:8000` (Caddy HTTPS); the raw API is
  `http://localhost:8002`.
- `wmill` lives in the default devshell (`nix develop --command wmill`).
  If auth expires: BOOTSTRAP.md flow (admin login against :8002, then
  `wmill workspace add kdevops kdevops http://localhost:8002/ --token ...`).
- VM `kunit-check` may still be running with the validated kernel.
- Deploy split: `wmill sync push` for `f/**` (push-only, never pull);
  `nix run .#windmill-install` after ANY `vendor/` edit (re-syncs the
  workbench copy workers read); kernel-config changes need a kernel
  rebuild; closure-module changes need a closure rebuild + VM refresh.

## Hard-won gotchas (verified this session, cost real time)

- `render_all` must be the SOLE key of a step result or the tables do not
  render (`DisplayResult.svelte:258`); the verdict travels through the
  per-item collect results into `judge` instead.
- systemd logs SUCCESSFUL process exits at debug level: never require a
  visible exit-status journal record on a clean run; the `Finished` job
  record is the proof (`unit.c` `unit_log_process_exit`).
- Unit state is useless for sub-second oneshots (instances are GC'd);
  journal cursors are the run identity.
- The nixpkgs xfstests recipe REPLACES `patchPhase`, silently ignoring both
  `postPatch` and `patches`; append to `patchPhase`.
- `nixfmt` reflows multi-line strings in `.nix`; shell fragments with
  literal tabs belong in their own files (or ship a real `.patch`).
- `$TMPDIR` is often EMPTY inside `nix develop` shells and under
  `dangerouslyDisableSandbox`; always use the session scratchpad path
  explicitly (a bare `$TMPDIR/x` becomes `/x`).
- `git commit --amend` commits the WHOLE index: never amend with unrelated
  staged files (this bled vendor files into a docs commit once).
- Windmill passes `null` for absent `?.` transforms; Python sees `None`
  overriding declared defaults on optional knobs (schema defaults are
  applied server-side for SOME shapes; do not rely on it, guard in code).
- History rewrites: cherry-pick rebuild onto `336b37a` with per-commit
  `git checkout <wip-branch> -- <paths>` folds, verify
  `git diff --quiet HEAD <wip>` = TREE IDENTICAL, run all gates, then
  `git push --force-with-lease origin main`.
- The flake `generated` check runs from the git-TRACKED tree: untracked
  new files make it fail with confusing "No such file" errors; `git add`
  first.

## The recipe for the next suite (short form; the RST is the law)

1. Kernel: fragment (+ `builtin/` mirror) in
   `vendor/linux-config-fragments`, `verify_config.sh` on a merge.
2. Guest: `vendor/nixos-flake/modules/testSuites/<suite>.nix` (templated
   oneshot, no RemainAfterExit, TimeoutStartSec=infinity + comment,
   journal+console, StartLimitIntervalSec=0, Documentation=; scopes with
   RuntimeMaxSec if per-test control is needed; boot-prep unit on the
   kmod-static-nodes pattern if registration needs loading); register in
   the vendored flake + its checks; overlay/pkgs for userland; share only
   if files are exchanged.
3. Register in `_TEST_SUITES` (`f/nix/render_config.py`).
4. Flow `f/<suite>/`: discover (gate, enumerate, refuse-empty, cache for
   pickers) → optional render_config/prepare/wipe → forloop
   (start/wait/collect, skip_failures true) → report (render_all sole key,
   report.json) → judge (run_status shared rule) + failure stop. Copy the
   invariants from `f/kunit` (cursor identity, plan/stats validation,
   crashed+timed_out gating, host-liveness, vm/vm-run tags).
5. Docs: `docs/flows/<suite>.rst` staged orphan, UI-first, guests.rst
   linked, upstream links + `:src:` on the unit sources.
6. Gates: `nix flake check`, `scripts/check-style.sh`, vendored checks,
   Sphinx `-W`; deploy; validate LIVE end to end before calling it done.
