# Handoff: firmware per-scenario systemd units (deferred enhancement)

Status: **SKIPPED for now, by user decision.** The existing
`kselftest@firmware` integration is full coverage; this document exists
only in case we later want the per-scenario decomposition. If coverage
is the question, the answer is already settled: nothing is missing.

## Repo and baseline

- Repo: `/home/dagomez/src/kdevops-ng`, branch `main`.
- The firmware kselftest collection is integrated, live-validated, and
  committed as the series ending near `f52efdf` (subjects:
  `test: add the firmware collection prerequisites`,
  `defconfigs: make the imageless preset firmware-ready`,
  `modules: create /lib/firmware for the firmware tests`,
  `selftests: cover the kselftest firmware collection`,
  `docs: note the firmware collection's guest needs`).
  `git push` to origin is a USER action (permission classifier blocks
  the agent); check whether it happened.
- The original integration plan (complete, executed):
  `/home/dagomez/.claude/plans/misty-churning-kettle.md`.
- Project memories to read first: `preset-maximize-modules.md`,
  `selftests-suite-executor.md`, `test-suite-requirements-doc.md`, and
  the in-tree `notes/handoff-add-test-suite.md` plus
  `docs/contributing/test-suites.rst` (the canonical suite spec).

## The settled analysis (do not re-derive)

### Terminology, per the user

"Wrapper-free" means systemd `ExecStart` calls the upstream-provided
entry point directly (fstests `./check`, `run_kselftest.sh`, a
debugfs/sysfs write), with env/prereqs expressed as unit directives.
It does NOT mean bypassing upstream wrappers. Never insert a
hand-written bash shim between systemd and the tool. Everything current
already satisfies this.

### Why per-scenario units add no coverage

`tools/testing/selftests/firmware/fw_run_tests.sh` (v7.2-rc1,
`~/wt/vanilla/linux`) is not a dispatcher; it owns real test logic:

- Runs the compiled `fw_namespace` binary once (needs `/lib/firmware`
  to exist; our `selftests.nix` tmpfiles rule provides it).
- Then runs the trio `fw_filesystem.sh` + `fw_fallback.sh` +
  `fw_upload.sh` THREE times, once per emulated kernel config ("rare"
  USER_HELPER=n, "distro" USER_HELPER=y, "android" FALLBACK=y), by
  flipping `/proc/sys/kernel/firmware_config/{force,ignore}_sysfs_fallback`
  between rounds. Total: 10 scenario-runs.

A `firmware@<scenario>` template calling the sub-scripts directly would
run each under ONE sysctl config unless the 3-config matrix is
replicated as ~10 unit instances with `ExecStartPre` sysctl writes,
duplicating upstream orchestration that will drift. Gains are
observability only: per-scenario verdicts/rows, per-cell rerun. The
collection log already labels each section, so failures self-localize.

### Irreducibility facts (from the systemd research agent)

- systemd removed userspace firmware loading in v217/2014 (commit
  `be2ea723b1d0`); no udev firmware rule exists; systemd README says
  production kernels should set `FW_LOADER_USER_HELPER=n`. Our `=y` is
  test-only and correct; nothing on a NixOS guest races the fallback.
- Genuinely single-write (KUnit-like): only `trigger_request` /
  `trigger_async_request` on
  `/sys/devices/virtual/misc/test_firmware/` (write's errno = verdict).
- Irreducibly interactive: fallback (must poll for the kernel-created
  `<name>/loading` node mid-request), upload (polls the
  preparing/transferring/programming `status` state machine), namespace
  (compiled C binary). These are why upstream ships scripts + a binary.
- Key sources: `lib/test_firmware.c` (triggers at ~666/767/812,
  `test_result` ~1383), `Documentation/driver-api/firmware/
  fallback-mechanisms.rst`, `Documentation/ABI/testing/
  sysfs-class-firmware`, `tools/testing/selftests/firmware/*`.

## If the enhancement is ever pursued

1. Prefer the upstream route: split `TEST_PROGS` upstream so
   `run_kselftest.sh --test firmware:<scenario>` addresses scenarios;
   local units then follow for free via the existing `kselftest-test@`
   template. This avoids duplicating the config matrix locally.
2. If done locally anyway: a `firmware@<config>-<scenario>` template in
   `vendor/nixos-flake/modules/testSuites/selftests.nix` (or a new
   module), `ExecStartPre` tee writes to the two sysctls,
   `Requires=modprobe@test_firmware.service`, `ConditionPathExists=
   /sys/devices/virtual/misc/test_firmware`; a small `f/firmware` or
   extension of `f/selftests` per `docs/contributing/test-suites.rst`
   (discover/start/wait/collect/report/judge, cursor-scoped, `render_all`
   sole report key, numeric `time(s)` column). Follow the vendored
   subproject's own commit rules.
3. Known red to expect: `fw_upload` fw1 readback mismatch after
   cancel/error/too-big churn (deterministic; upload round-trips fine in
   isolation). Recorded as an upstream finding candidate; do not paper
   over it.

## Suggested skills

- `kernel` (read `technical-patterns.md` first) when touching kernel
  sources or judging test semantics.
- `nix` (`references/nixos-modules.md`) for unit/module work in
  `vendor/nixos-flake`.
- `cli-commands` for `wmill` deploy (push-only; never `sync pull`).

## Gates before any commit

`nix flake check` and
`nix develop .#checks --command bash scripts/check-style.sh`; scoped
`git add` by explicit path only (concurrent sessions edit this repo).
