<!--
Status: PLANNED, not started (2026-07-19).
Proposal reviewed twice: once as a design discussion, once against the
Nix source at ~/src/nix/nix (2.2-17270-g3887a906b). Verified facts
below are source-verified; do not re-derive them.
Related: notes/adr/0005-custom-store-not-nix-store.md (the transport
this plan extends), f/common/store.py (the mechanism), memory
grafana-monitoring-stack (the dashboards phase builds on it).
-->

# Publish test results to the Nix store for every suite

## Context

The project already moves build artifacts (kernel run layers, QEMU
prefixes, kernel devel layers) through the Nix store: `f/common/store.py`
adds a tree with `nix store add-path`, registers an identity to
store-path index entry under `SYSTEM_DIR/store-index/<name>` (the entry
doubles as an indirect GC root), and peers fetch with `ssh readlink` +
`nix copy` (ADR 0005). Suite results today live outside all of that: raw
artifacts on the shared filesystem (for fstests,
`<share>/<vm>/<kver>/results/<section>`), parsed summaries only in
Windmill job results, and telemetry in Prometheus/Loki.

This plan makes results a third artifact class in the same store index,
for all five suites (fstests, kunit, selftests, runtime_tests,
usertests), then layers on top of it: report replay into BOTH consumers
(the Grafana dashboards and Windmill's rich display rendering) with
zero execution, distribution of result bundles through git, and a
flakiness model where runs are immutable observations and flake
verdicts are a separate, derived artifact.

## Verified facts (against the Nix source; do not re-derive)

- **`nix store add` records no references.** `src/nix/add-to-store.cc`
  calls `addToStoreSlow(name, path, method, algo, {})`; the `{}` is the
  `StorePathSet references` parameter, and the CLI exposes no flag to
  set it. Reference scanning happens only when a derivation builds in
  the sandbox. Consequence: `nix copy` of a results bundle does NOT
  carry the tested kernel along, and `nix path-info --json` shows an
  empty reference set. Provenance must be recorded as data in a
  manifest, not expected from the reference graph.
- **References would change the path anyway.** `makeType()` in
  `src/libstore/store-dir-config.cc` hashes any references into the
  store-path fingerprint. So manifest-as-data is not a workaround for
  the missing CLI flag; it is required to keep the path a pure function
  of content, which the git distribution phase depends on.
- **The store path is deterministic across hosts.** For the default
  ingestion (NixArchive, SHA-256, no references) the path is
  `makeStorePath("source", narHash, name)`, a SHA-256 over
  `source:sha256:<narhash>:/nix/store:<name>`. Same bytes + same
  `--name` + the standard `/nix/store` store dir reproduce the identical
  store path on every host. A tree distributed by git re-enters the
  store at the exact recorded path.
- **`add-path` is a deprecated alias.** `CmdAddPath` in
  `add-to-store.cc` is registered as a deprecated alias of
  `nix store add`, with identical defaults (NixArchive, SHA-256), so
  switching produces byte-identical store paths.
- **Indirect GC roots are real and already battle-tested here.**
  `LocalStore::addIndirectRoot` (`src/libstore/gc.cc`) backs the
  `nix build <sp> --out-link <entry>` pattern `store.publish` uses;
  kernels published this way have survived `nix store gc` in
  production.
- **Results are non-regenerable and never deduplicate.** Every run is
  unique bytes; the store is a transport and integrity layer for them,
  not a space saver, and GC of an unpublished bundle is data loss.
- **Windmill's rich display contract** (verified in the fork's
  `frontend/src/lib/components/DisplayResult.svelte`,
  `~/src/windmill-labs/windmill`): a result whose only key is
  `render_all` with an array renders each element by its own kind, and
  a single-key result of `table-col` / `table-row` /
  `table-row-object` / `markdown` / `html` / an image kind renders
  richly. The suite report steps already return this shape, so a
  bundle that stores the payload verbatim can re-render it in any
  Windmill job without recomputing anything.

## Design decisions

- **Observation vs determination.** A run publishes one immutable
  observation bundle under a unique name. Nothing ever overwrites a
  bundle. Flakiness (and any other cross-run judgment) is a separate
  derived artifact produced by an explicit qualify step over N named
  observations. The first observation never becomes canonical.
- **Naming.** Observations: `results-<suite>-<uts_release>-<job_id>`
  (the Windmill job id makes it unique and joins it back to the job
  log). Verdicts: `verdict-<suite>-<uts_release>-<job_id>` where the
  job id is the qualify run's own.
- **Manifest as the provenance edge.** Every bundle carries a
  `manifest.json` recording: schema version, suite, run window
  (`started_realtime_ms`/`ended_realtime_ms`), VM name, kernel release,
  the tested artifacts by index name AND store path (kernel, QEMU,
  closure toplevel when known), the parsed verdict summary, and for
  verdict bundles the list of observation names they judged. Provenance
  queries walk manifests through the index, not the Nix reference
  graph.
- **Retention follows publication.** The store index is the system of
  record only until a bundle is committed to the results git repo;
  after that the bundle is regenerable (git checkout + re-add), so its
  GC root may be pruned. Pruning is always an explicit app run, never
  automatic.
- **Compress at staging time; the store keeps bytes as-is.** The local
  Nix store holds trees uncompressed and `nix store optimise`
  deduplicates only identical files, which unique per-run results
  never are, so storage economy must come from the bundle itself.
  Every evidence file (logs, KTAP/xunit, diffs, journal extracts,
  OpenMetrics) is stored xz-compressed (`<name>.xz`), written through
  Python's stdlib `lzma` module so every step and the render/import
  consumers can read it back with no extra dependency (zstd enters the
  stdlib only in Python 3.14). Only `manifest.json` stays plain, so
  index listing, pickers, and `grep` over checkouts stay cheap.
  Compression happens once, in the shared `stage()`, so no suite can
  forget it. Path determinism is unaffected: the publisher compresses
  once and the compressed bytes themselves travel (via `nix copy` or
  git), so re-adding reproduces the recorded path regardless of xz
  version.

## Phase 0: groundwork

Switch `f/common/store.py` (and its module docstring's equivalent-bash
block) from `nix store add-path` to `nix store add`, and update the
CLAUDE.md unified-CLI sentence to match; upstream deprecated the alias
and the resulting paths are identical, so this is a rename with no data
migration. Then write ADR 0012 "test results as store-published
observations" capturing the verified facts and design decisions above.
Two commits (`common:` and `docs:`/`adr:`).

DoD: `grep -rn 'add-path' f/ scripts/ docs/ CLAUDE.md` shows only the
deprecation note in the ADR, if that; `nix flake check` green.

## Phase 1: shared results library

New `f/common/results.py` (a library module like `store.py`, with its
`.script.yaml`/`.lock` sidecars authored the same way as the other
`f/common` modules):

- `stage(suite, name, files)`: build the bundle tree in a scratch dir.
  Takes an explicit source-to-relative-dest map so each suite decides
  its layout; prints `copied <src> -> <dest>` per file per the
  conventions. Symlinks are never followed (`symlinks=True` lesson from
  publish_devel). Every staged file is written as `<dest>.xz` through
  `lzma.open` at preset 9 (`manifest.json` excepted); the printed line
  carries the raw and compressed sizes so the job log shows what
  compression bought.
- `write_manifest(tree, manifest)`: validate the schema-versioned dict,
  write `manifest.json`, print `wrote <path>`.
- `publish_results(name, tree)`: thin wrapper over `store.publish`.
- `list_results(suite=None)` / `load_manifest(name)`: pure reads over
  `store.list_index("results-")`, resolving through
  `store.local_path`; back future dynselect pickers, so they must never
  raise or mkdir (the `reuse_check` purity rule).

Manifest schema v1 lives here as a documented dict-builder function so
every suite constructs it the same way. Unit-test the staging map and
manifest validation with fixtures (same style as the resolve/list_index
fixture tests).

DoD: fixtures pass, `nix flake check` + `check-style.sh` green,
pyright 0 errors.

## Phase 2: fstests as the worked example

New step `f/fstests/publish_results.py` wired into `check.flow` after
`report` (and after the monitoring annotate step, so the manifest can
record the annotation window). Bundle contents per run:

- per-section `result.xml`, `check.log`, and any `.out.bad` diffs
  (paths already surfaced by `f/fstests/collect`),
- `report.json`: the report step's return value stored VERBATIM. This
  is the rich display payload (the `render_all` / table structures the
  Windmill UI renders), so re-rendering later is replay, not
  recompute,
- the judge verdict,
- section geometry / `xfs_info` capture from `prepare`,
- `manifest.json`.

The tested-kernel provenance comes from the flow's existing inputs (the
resolved store index name when the kernel was a store pick; the release
string always). A `publish_results: bool` flow knob (default on) gates
the step. The step must degrade like `collect` does: a crashed or
timed-out run still publishes (the bundle records the failure), a
missing artifact is flagged in the manifest rather than raising.

DoD: a live fstests run on a real VM publishes
`results-fstests-<kver>-<job_id>`; `nix path-info` resolves it;
`load_manifest` returns the verdict; a second host fetches it with the
existing peer transport (`nix copy`) untouched.

## Phase 3: roll out to the other four suites

Add the equivalent publish step to `f/kunit/run.flow`,
`f/selftests/run.flow`, `f/runtime_tests/run.flow`, and
`f/usertests/run.flow`. Each suite defines only its staging map; the
library does the rest. Evidence differs per suite and the map must
match how each suite actually collects:

- kunit: the cursor-scoped KTAP extract and parsed suite tables from
  `collect`/`report`, plus the judge verdict.
- selftests: the runner summary output collected over the journald
  socket, per-test results, negative-path evidence.
- runtime_tests: the kmsg evidence window and per-class module load
  state (the verdict basis, since exit status is unobservable under
  `modprobe@`).
- usertests: per-harness stdout/stderr captures, the
  `expected_assert_re` whitelist hits, sanitizer findings.

Every suite additionally stores its report step's return value
verbatim as `report.json` (the rich display payload), exactly like
fstests in Phase 2. Verify each suite's actual artifact locations from
its `common.py` before writing the map; do not trust this plan's
sketch. One commit per suite.

DoD: one live (or replayed) run per suite publishes a bundle whose
manifest round-trips through `load_manifest`; all five suites share the
identical manifest schema.

## Phase 4: report replay in Grafana and in Windmill

Goal: a user with a bundle and this repo sees the run's reports in both
consumers without running any suite: the Grafana dashboards over the
run window, and the suite's rich report tables inside Windmill.

Windmill side first, since it is pure replay:

- New step `f/workbench/render_results.py`: a `bundle` dynselect picker
  backed by `results.list_results()` (labels from each manifest: suite,
  kernel release, verdict, date), which loads the picked bundle's
  `report.json.xz` from the store (stdlib `lzma`) and returns the
  payload unchanged, prepended with
  a `markdown` element summarizing the manifest (suite, kernel, tested
  artifact names, run window, verdict). Because the payload is stored
  verbatim in the rich display shape, the job's result panel renders
  the same tables the original run showed. Read-only, never raises on
  a missing optional artifact (it reports the gap as a row instead).
- The qualify verdicts (Phase 6) render through the same step: a
  verdict bundle's `report.json` is its per-test outcome table.

Grafana side:

- At publish time, export the run window from Prometheus over its HTTP
  API into OpenMetrics text and add it to the bundle
  (`metrics.openmetrics`). The window comes from the same
  `started_realtime_ms`/`ended_realtime_ms` the annotate step already
  uses, padded by a small margin.
- New app `nix run .#results-import <bundle-name-or-store-path>`:
  backfills the OpenMetrics file into the local provisioned stack with
  `promtool tsdb create-blocks-from openmetrics` (a supported,
  documented round trip), re-posts the run annotation through the
  Grafana API from the manifest, and prints where to look.
- Loki has no supported backfill: journal extracts ship as plain files
  in the bundle and the import app says so instead of pretending.
- Document the flow on the monitoring page.

DoD: on a host that never ran the suite, running
`f/workbench/render_results` against a fetched bundle shows the
original run's report tables in the Windmill result panel; and
`nix run .#monitoring-deploy` plus `nix run .#results-import <bundle>`
shows the guest-overview dashboard populated for the run window, with
the run annotation present.

## Phase 5: distribute bundles through git

Because the store path is a pure function of NAR bytes + name, git can
carry the plain tree and every consumer re-materializes the identical
store path locally. No NAR files, no binary-cache layout, and git
compresses the mostly-text bundles well.

- A dedicated results repository (NOT this repo; unbounded per-run
  growth does not belong in the code repo history). Layout:
  `<suite>/<uts_release>/<bundle-name>/` holding the tree verbatim
  (evidence files arrive already xz-compressed from staging, so the
  repository stays small without relying on git delta compression),
  plus a top-level `INDEX.md` generated line per bundle.
- Publisher side: an app (`nix run .#results-export`) that copies a
  named bundle out of the store into a checkout of the results repo and
  commits it (subject `results: <bundle-name>`), recording the expected
  store path in the manifest (it is already there) and in the commit
  body.
- Consumer side: `results-import` (Phase 4) grows a git mode: given a
  checkout, `nix store add --name <bundle-name> <tree>` and verify the
  produced path equals the manifest's recorded path; a mismatch is a
  hard error (corrupted or tampered tree). Then `store.link_local` it
  so the local index sees it like any fetched bundle.
- Retention closes the loop: `nix run .#results-prune` may drop the GC
  root of any bundle whose name exists in the results repo, and must
  refuse for one that does not. Never automatic.

DoD: bundle published on host A, committed, cloned on host C (no ssh
peering to A), imported; store path matches the manifest; dashboards
replay per Phase 4.

## Phase 6: flakiness as a derived verdict

- Escalate on failure, do not run everything N times: a run keeps
  default N=1; when a test fails, the suite's flow reruns just that
  test K times (fstests `./check` takes individual tests, kunit already
  has cursor-scoped runs, selftests and the module suites invoke
  per-test). Each rerun publishes its own observation bundle with
  `repeat_of` naming the original observation.
- K is a curated form field with a global default (start with K=2),
  plus a per-test override table as the gated advanced input (the
  curated-forms convention; per-test tuning is real, a 4-minute kmod
  test and a 2-second sysctl test do not deserve the same K).
- The determination point is a new `qualify` step (shared logic in
  `f/common/results.py`, thin per-suite wiring): it loads the 1+K
  observations for each failed test, emits
  `verdict-<suite>-<uts_release>-<job_id>` with per-test outcomes:
  mixed results are `flaky`, all-fail is `regression`, and the manifest
  lists every observation name it judged.
- The verdict feeds monitoring: one more annotation tag
  (`verdict:flaky` vs `verdict:regression`) on the existing suite-run
  annotations.

DoD: a seeded intermittent failure (or a replayed known-flaky) yields a
verdict bundle marking it flaky while a consistent failure yields
regression; both visible as annotation tags.

## Sequencing and repo rules

Phases 0 to 3 are the core and land in order; 4, 5, 6 are independent
of each other and can land in any order after 3. All work on `main`
(the branch-bound deploy model), scoped `git add` by explicit path,
push-only `wmill sync push` deploys performed by the user, commit rules
per CLAUDE.md (atomic, `subsystem: summary`, Generated-by +
Signed-off-by, `nix flake check` + `check-style.sh` before each
commit).
