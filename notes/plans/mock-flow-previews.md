<!--
Status: PLANNED, not started (2026-07-20).
Staged as the third coverage layer after the fixture-test fan-out and the
preview-smoke stragglers landed. Prerequisite reading:
docs/contributing/testing.rst (the layers this extends) and the
source-verified Windmill facts in the flow-testing-layer memory.
-->

# Mock-based previews for the heavy flows

## Context

Seven flows cannot join the preview smoke suite as they stand: the kernel,
QEMU, and closure builds, boot, bringup, bisect, and workbench init all
mutate host state or need hardware, so only their pure probe and resolve
steps are smoke-covered today. What remains untested in them is the flow
WIRING: the input transforms, the branch and loop gating, the failure
modules, and the plumbing of one step's result into the next. Windmill's
step mocking covers exactly that gap: a module whose `mock.enabled` is set
returns `mock.return_value` without executing, while every transform and
gate around it runs for real.

## Verified facts (from the deployed fork's sources; do not re-derive)

- `FlowModule.mock` (`{enabled, return_value}`) is an OpenFlow field
  (`backend/windmill-types/src/flows.rs`), and the WORKER honors it, in
  previews and in normal runs alike (`worker_flow.rs` short-circuits a
  mocked module to its `return_value`).
- Because the worker honors mocks in normal runs too, a mock committed
  into a deployed `flow.yaml` would stub the production flow. Mocks must
  therefore be injected at preview time only, never committed.
- The preview endpoint takes an arbitrary flow definition: `POST
  /w/{ws}/jobs/run_wait_result/preview_flow` with `{value, args}` runs an
  undeployed flow value, so a harness can load the local `flow.yaml`,
  inject mocks, and run the result without touching the repo or the
  workspace. The CLI equivalent is `wmill flow preview` on a staged
  temporary `.flow` directory.
- A flow preview job's per-module status is queryable afterwards
  (`wmill job get <id>` shows the step tree), so assertions can cover
  which branches ran, not just the final result.

## Design decisions

- **Mocks are harness-injected, never committed.** The committed
  `flow.yaml` stays mock-free; the harness deep-copies it, sets
  `mock.enabled` + `mock.return_value` on a declared list of module ids,
  and previews the copy from a scratch directory.
- **Mock values come from the fixture contracts.** Each mocked step's
  `return_value` is the same shape the fixture tests assert for that
  step's real output (a kernel manifest, a boot access manifest, a bisect
  verdict), sourced from one fixtures module shared with `tests/` so the
  shapes cannot drift apart.
- **Real steps stay real where they are safe.** A mocked-flow preview
  still executes the pure steps (`reuse_check`, `resolve`, `build_flags`,
  the verdict machinery) so the wiring test also exercises them in
  context.

## Phase 1: the harness mode

Extend `scripts/preview-smoke.py` (or add `scripts/preview-flows.py` if
the case tables grow past readability) with a mocked-flow mode: load a
flow's `flow.yaml`, inject the case's mocks, stage the modified `.flow`
directory under the scratch dir, run `wmill flow preview` on it with the
case's args, and assert on the final result. Wire it as
`nix run .#preview-flows`, a separate app from the per-step smoke since a
whole-flow preview is heavier.

DoD: one flow (workbench init, the simplest) previews end to end with all
three steps mocked, asserting the flow result carries each mocked step's
contribution.

## Phase 2: per-flow mock tables

One case table per heavy flow, mocking only the mutating modules:

- `f/kernel/build`: mock the worktree, compile, install, and publish
  steps with manifest-shaped values; `reuse_check` and `build_flags` run
  real. Assert the final build manifest plumbing.
- `f/qemu/build` and `f/nix/build`: same pattern.
- `f/qsu/boot`: mock the two render steps, drive creation, and the boot
  wait; assert the access-manifest composition from the mocked returns.
- `f/qsu/bringup`: exercise the component modes: a reuse-mode preview
  runs `resolve` real against a fixture store index and mocks the boot
  tail; a build-mode preview asserts each build subflow is entered.
  Regenerate awareness: the flow is generated, so the case table keys on
  module ids that `scripts/gen-bringup.py` emits, and the `generated`
  check keeps them stable.
- `f/kernel/bisect`: the highest-value target: mock the payload and boot
  steps and drive the whileloopflow verdict state machine through a
  scripted GOOD/BAD sequence, asserting the loop terminates with the
  expected culprit bookkeeping. Cap iterations in the case args so a
  regression cannot run away.
- `f/workbench/init`: from phase 1.

DoD: every heavy flow has at least one green mocked preview; the bisect
state machine case walks a full synthetic bisection.

## Phase 3: branch coverage assertions

Grow the harness to fetch the preview job's step tree after the run
(`wmill job get`) and assert which modules ran versus were skipped, so
mode gating (bringup's build-versus-reuse groups, bisect's verdict
branches) is asserted structurally, not inferred from the result.

DoD: a bringup reuse-mode case asserts the build subflows were skipped
and the boot tail ran; a bisect case asserts the loop iteration count.

## Phase 4: documentation and gate placement

Document the mode on docs/contributing/testing.rst as the fourth layer
(wiring coverage for flows the smoke suite cannot run), including the
never-commit-mocks rule and the shared-fixtures contract. It stays an
on-demand `nix run` app like the smoke suite; it does not join the
hermetic gate since it needs the instance.

## Sequencing and repo rules

Phases land in order; each is one or two atomic commits on `main` with
the usual gates (`nix flake check`, `check-style.sh`) and scoped staging.
The case tables grow suite by suite; a new heavy flow joins with its mock
table in the same change that adds the flow, mirroring the rule the
test-suites spec sets for the other layers.
