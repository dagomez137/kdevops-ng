<!--
Status: PLANNED, not started (2026-07-19).
Proposal reviewed twice: once as a design discussion, once against the
Nix source at ~/src/nix/nix (2.2-17270-g3887a906b). Verified facts
below are source-verified; do not re-derive them.
Third review 2026-08-13: the comparison-semantics evaluation. The
manifest schema, the requested/executed/notrun/truncated encoding, and
the comparison rules below were validated by generating manifests
retroactively from the real vanilla/writethrough A/B runs of section
xfs_full_bs16k_ss16k and comparing them (see "Validated against real
runs" at the end). The corpus-mismatch incident that motivated the
identity floor is fixed at its source (f/nix/render_config now rejects
a ref on a path-type override) but the manifest must still record
corpus identity so a mismatch can never again be silent.
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
  `~/src/windmill-labs/windmill`; the fork's display path is
  byte-identical to upstream at the 1.785.0 merge-base, so upstream
  semantics apply): a result whose only key is `render_all` with an
  array renders each element by its own kind, and a single-key result
  of `table-col` / `table-row` / `table-row-object` / `markdown` /
  `html` / an image kind renders richly. The suite report steps
  already return this shape, so a bundle that stores the payload
  verbatim can re-render it in any Windmill job without recomputing
  anything. Load-bearing details for anything new that renders:
  - `render_all` bypasses the client size gates and each element is a
    child `DisplayResult` with its own budget; elements must each be a
    complete single-key rich value.
  - The `markdown` kind renders with no GFM plugin chain: markdown
    tables do not render, and embedded HTML is escaped. Tables must
    use the table kinds; colored views must use the `html` kind
    (DOMPurify keeps `table`/`div`/`span` and inline `style`, so
    inline-styled HTML is the one path to semantic color).
  - Use the explicit `{"table-row-object": [...]}` wrapper with a
    leading header-row array: it is the only correct column-ordering
    path, and it bypasses the 50-column and large-object gates. A
    column absent from the header row is dropped from the data.
  - The practical inline ceiling is the API's 90 kB truncation of the
    stored jsonb (`pg_column_size`, TOAST-compressed): a bigger result
    renders as `WINDMILL_TOO_BIG` with only a download link, for flow
    steps and top-level flow results alike. Within that, keep any one
    markdown string under ~50 k characters (the 100 kB
    `roughSizeOfObject` gate counts 2 bytes per character). Table
    cells truncate at 100 characters with a hover popover, so long
    messages belong in evidence files, not cells.

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
  `manifest.json` recording the full identity floor: schema version,
  suite, run window (`started_realtime_ms`/`ended_realtime_ms`), VM
  name, kernel release, the tested artifacts by index name AND store
  path (kernel, QEMU, closure toplevel when known), the suite corpus
  identity, the Windmill job ids, the request that produced the run,
  the per-item and per-test outcome maps, the parsed verdict summary,
  and for verdict bundles the list of observation names they judged
  (see "Manifest schema v1"). Provenance queries walk manifests
  through the index, not the Nix reference graph.
- **Corpus identity is part of run identity, captured from the
  closure only.** The A/B that motivated the comparison work silently
  ran two different xfstests corpora; the f/nix/render_config ref-drop
  that caused it is fixed, but the manifest must make a repeat
  impossible to miss. Every bundle records the suite corpus (for
  fstests, the xfstests package) as the package store path scanned
  from the closure toplevel's requisites (the same
  `nix path-info --recursive` walk the `list_groups` dynselect already
  uses on `vars.json`'s `closure.toplevel`), identified by the
  group-registry doc probe rather than the name match alone; no match
  records the corpus as absent, multiple matches record them all. The
  rev comes from the same store path: the override overlay bakes
  `+git<shortRev>` into the package version, so the path name carries
  the short rev, resolvable to a full rev through the Bare; an
  unsuffixed version is the vendored pin, and a `+src` suffix is a
  path snapshot, which honestly has no rev. The per-VM config's
  `flake.lock` must NOT be consulted at publish time: it is per-worker
  mutable state that f/nix/lock_config re-locks to the branch tip on
  every build, so it names the latest build, not the run being
  published. The 2026-08-13 live validation hit exactly this: a
  running closure whose override had already vanished from the
  re-rendered config dir.
- **The request is data.** A run's manifest records what was ASKED,
  not just what happened: the verbatim `./check` argument string and
  the sections requested, rendered, and skipped with reasons, or for
  the other suites the resolved iterator item list plus the repeat
  count. f/fstests/render_config's return alone under-represents the
  request: it silently drops a requested section name absent from the
  catalog, and with `arm_all_sections` its `skipped` list also holds
  armed-only sections nobody requested, so the publish step captures
  the verbatim `sections`/`local_config` form inputs too (or the
  return grows a `requested` key) and separates run-selection skips
  from arm-all skips. The requested test universe is not
  pre-enumerable (resolution happens inside the guest), so the
  manifest also records the resolved sets a complete run reports; a
  truncated run's artifacts cannot name what it would have run, which
  is exactly why the request expression must be captured at publish
  time.
- **Per-test outcomes live in the manifest, plain.** The whole point
  of a bundle is the join over test ids; making every comparison,
  qualify step, and history query decompress evidence files first
  would tax the common path to spare the rare one. Measured on the
  real 1277-test section: the per-test map costs ~112 kB plain
  (~6.5 kB in the git pack), which is acceptable for grep, pickers,
  and diffs. Failure messages are capped at 200 characters in the
  manifest (the full text stays in the evidence files); the Windmill
  table cell truncates at 100 characters anyway.
- **Publish never overwrites.** `store.publish` and `link_local`
  silently repoint an existing index entry; the immutable-observation
  rule is enforced by the results layer, whose publish wrapper
  refuses a name that `local_path` already resolves.
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

## Manifest schema v1

One schema for all five suites, at each suite's honest granularity:
an ITEM is the suite's unit of run (an fstests section, a KUnit
suite, a kselftests collection or collection:test unit, a
runtime-tests module, a usertests harness), and a TEST is the finer
unit where one exists. The two-level shape is load-bearing: a kunit
or selftests item that crashed or produced an unparseable report has
an item-level `failed` entry and ZERO test rows, so a flat per-test
map would be empty exactly when the run failed worst.

```
manifest.json (plain JSON, everything else in the bundle is .xz):
  schema: 1
  suite: fstests | kunit | selftests | runtime_tests | usertests
  bundle: results-<suite>-<uts_release>-<job_id>   (its own name)
  vm: <vm name>
  kernel:  {release, store_path?, index_name?}
  corpus:  {store_path?, locked_rev?, origin?}
  window:  {started_realtime_ms, ended_realtime_ms}
  jobs:    {job_id, root_flow_job_id, flow_path}
  request: suite-specific, verbatim (fstests: check_args string plus
           sections requested/rendered/skipped-with-reason and the
           local_config text hash; others: the resolved iterator item
           list and repeats count)
  items:   {item_name: {
             status: passed | failed | notrun,
             crashed, timed_out,
             truncated?, report_present?, (derived per suite: only
                                          kunit/selftests/fstests
                                          have observables for them)
             runtime_s?, runs?,          (repeats fold: per-run
                                          status/runtime samples,
                                          median runtime_s)
             counts: {tests, passed, failed, notrun},
             geometry?,                  (fstests: configured +
                                          realized, device roles)
             extras?,                    (usertests seed and abort
                                          signal, runtime-tests
                                          module load state, ...)
             tests: {test_id: {status: passed | failed | notrun,
                               time_s?, reason?, message?,
                               runs?, fails?}}}}
  verdict: {status, failures: [...]}    (the judge summary)
```

One status vocabulary at both levels (`passed`/`failed`/`notrun`, the
strings every existing parser already emits); two spellings for one
enum across two nesting levels would invite silent mapping bugs. The
per-test `runs`/`fails` slots carry fstests' own `-i` iteration fold
(the parser reports per-test run and fail counts and a section-level
iteration count), so an iterated run's per-pass detail is not
evidence-file-only.

Schema rules the suites forced:

- `notrun` is never ignorable. It means three different things across
  the suites (KTAP `# SKIP`, missing kmsg evidence from a stress
  module, an environment gate), every suite's own `run_status`
  refuses to count a notrun item as a pass, and runtime-tests
  deliberately classifies a systemd condition-skip as FAILED. A
  consumer that reads notrun as "ignore" green-lights runs the suites
  fail on purpose.
- Test ids are composed where names are only locally unique: kunit
  test names are unique per suite, so the test id is `suite/test`;
  kunit's parameterised subtests are collapsed by the parser and stay
  invisible here, by its design. fstests test ids are section-scoped
  by nesting (the same test runs in many sections).
- Repeats (runtime_tests and usertests run one item N times) fold to
  one item entry carrying `runs` samples; `runtime_s` is the median,
  matching every existing consumer of the aggregated reports.
- Aggregate-only counts (an XArray module reporting 1e8 internal
  checks) live in `counts`, never as synthetic test rows.
- A step that died so hard it produced a nameless error object is
  still attributable: the suite loops are sequential with one ordered
  result slot per iterator element, so a dead iteration joins back to
  its item (and repeat index) by POSITION against the recorded
  resolved iterator list. That positional join is the authoritative
  recovery; request-minus-items only detects a truncated loop tail,
  and fails entirely under repeats (an item with one dead run out of
  N still appears in `items`). Both need the request encoding to be
  captured from the resolved iterator, not the raw form input.

## Comparison semantics

A comparison is a join over item and test ids between two manifests.
The rules, validated against the real A/B (see the validation section
at the end):

- **Scope to the requested intersection.** The comparison universe is
  the intersection of the two requested sets; everything outside it
  is reported as a set difference with per-status counts, never
  silently dropped and never allowed to flood the report as fake
  "absent" transitions.
- **Truncation is its own status.** A test requested but missing from
  a truncated run's records is `missing (run truncated)`, distinct
  from `notrun` (which the suite positively reported, with a reason).
  A comparison against a truncated run says so in its banner.
- **Identity mismatches lead the report.** Different corpus identity
  (or unknown identity, for pre-manifest runs), different section
  geometry, and truncation are printed before any per-test verdict,
  so a corpus drift can never again masquerade as a regression list.
- **The interesting buckets are transitions.** pass->fail (regression
  candidates), fail->pass (fixed), fail->fail (common failures),
  pass->notrun (coverage lost, with the notrun reason), notrun->pass
  (coverage gained), and notrun->notrun with a CHANGED reason
  (environment drift). Same-status tests are one summary count.
- **Normalize device names at compare time, never at capture time.**
  Cross-guest comparison showed pure device-naming noise (`nvme4n1`
  vs `nvme2n1`) in notrun reasons and geometry; the comparator
  canonicalizes device paths through the geometry's device-role map
  (TEST_DEV, SCRATCH_DEV, ...) when bucketing, while manifests keep
  the evidence verbatim.
- **Runtime swings are advisory.** Per-test runtimes exist in the
  xunit and are worth surfacing (>= 2x swing with a >= 10 s side), but
  wall clocks are polluted by host co-tenancy (two guests running
  suites concurrently measurably slow each other), so swings inform,
  never gate.
- **Flake handling stays in Phase 6.** A single comparison cannot
  distinguish flake from regression; transition candidates feed the
  targeted-rerun qualify model rather than growing verdict logic
  here.

## Phase 0: groundwork

Switch `f/common/store.py` (and its module docstring's equivalent-bash
block, in two places) from `nix store add-path` to `nix store add`, and
update the CLAUDE.md unified-CLI sentence to match; upstream deprecated
the alias and the resulting paths are identical, so this is a rename
with no data migration. The rename also reaches the six publish-step
equivalent-command docstrings (`f/kernel/publish.py`,
`f/kernel/publish_devel.py`, `f/kernel/publish_usertests.py`,
`f/kernel/publish_selftests.py`, `f/qemu/publish.py`,
`f/qemu/publish_devel.py`), the description text in
`f/common/store.script.yaml` (a semantic content change, not a style
edit, so hand-editing it is within the rules; the push-only model
forbids regenerating it with a pull), and the committed docs sources
`docs/concepts/build-store.rst` (two occurrences) and
`docs/concepts/terms.rst`. Then write ADR 0015 "test results as
store-published observations" capturing the verified facts and design
decisions above (0012 through 0014 are taken; this plan predates
them). Two commits (`common:` and `docs:`/`adr:`).

DoD: `grep -rn --exclude-dir=_build 'add-path' f/ scripts/ docs/
CLAUDE.md` comes back empty; `nix flake check` green.

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
- `build_manifest(...)`: the documented schema-v1 dict builder (see
  "Manifest schema v1") so every suite constructs it the same way,
  including the 200-character message cap.
- `publish_results(name, tree)`: thin wrapper over `store.publish`
  that first refuses a name whose index entry exists as a symlink AT
  ALL, dangling included (observations are immutable;
  `store.publish` alone would silently repoint the entry, and
  `store.local_path` reads a GC'd entry as absent, which is the wrong
  probe for a name that is taken forever). The load-bearing
  uniqueness mechanism is the job id in the name; a
  pruned-then-recreated name can only be caught downstream by
  results-import's store-path-versus-manifest check.
- `list_results(suite=None)` / `load_manifest(name)`: pure reads over
  `store.list_index("results-")`, resolving through
  `store.local_path`; back future dynselect pickers, so they must never
  raise or mkdir (the `reuse_check` purity rule). Picker labels come
  from each manifest (suite, kernel release, verdict, date), so
  `list_results` reads N manifests; they are plain JSON at the end of
  a local symlink, and the reads stay pure.
- `compare(baseline, candidate)`: the pure comparison core over two
  manifest dicts, implementing the rules in "Comparison semantics"
  and returning the bucketed transitions, set differences, identity
  warnings, and runtime swings as data (no rendering here). The
  session-validated prototype (`scripts/compare-fstests-runs.py`,
  which works on raw results directories rather than bundles) is the
  semantics reference; its comparison logic moves here behind fixture
  tests and the script becomes a thin wrapper or retires.

Unit-test the staging map, manifest validation, and the comparison
buckets with fixtures (same style as the resolve/list_index fixture
tests); the truncated-run and corpus-mismatch banners are fixture
cases, not afterthoughts.

DoD: fixtures pass, `nix flake check` + `check-style.sh` green,
pyright 0 errors.

## Phase 2: fstests as the worked example

New step `f/fstests/publish_results.py` wired into `check.flow` after
`report` (and after the monitoring annotate step, so the manifest can
record the annotation window). Bundle contents per run:

- per-section `result.xml`, the current attempt's `check.log` summary
  block, and any `.out.bad` diffs (paths already surfaced by
  `f/fstests/collect`). The flow's own `_rotate_results` renames the
  PREVIOUS attempt's files at the next run's start, so at publish
  time the unrotated `result.xml` is this attempt's by construction;
  the section's `check.log` appends across attempts, so publish
  slices its last summary block and validates it against the run
  (the xunit's test set, or the run window) before trusting it:
  `Interrupted!` is written by check's signal trap, so an interrupt
  or timeout leaves the marker but a guest panic leaves NO block at
  all, and since rotation is gated on `result.xml` existing while
  `start` deletes only `result.xml`, a stale block from an earlier
  xunit-less attempt can be sitting there unrotated. A block that
  fails validation is recorded as missing, not attributed. The xunit
  already holds per-test status, time, failure message, and the
  notrun reason (`skipped message`),
- `display.json`: the report step's return value stored VERBATIM.
  This is the rich display payload (the `render_all` / table
  structures the Windmill UI renders), so re-rendering later is
  replay, not recompute,
- `report.json`: the structured rollup the report step writes (status,
  per-section collect dicts, failures). These are two different
  artifacts: the step RETURNS the display tables and WRITES the
  rollup, and the bundle needs both. Publish consumes the path the
  report step returns as `report_json`, never a re-derived one: the
  write is conditional and falls back to the share root when the
  kernel version is unknown, exactly the degraded runs that must
  still publish,
- the judge verdict,
- section geometry: collect's MERGED `detail.geometry` (configured
  from the section config plus realized from the wait capture). The
  raw `<section>.geometry.json` on the share is realized-only, keyed
  by VM and section but not kernel, never rotated or pruned, and its
  capture is skipped when the guest crashed, so a crashed run can
  find a stale file from an earlier kernel there; publish treats it
  as absent unless it is fresh against the run window,
- `manifest.json`.

Identity capture at publish time:

- kernel: the release from `discover`, the store index name when the
  kernel was a store pick, and the closure toplevel store path from
  the VM sidecar `vars.json`,
- corpus: the xfstests package store path scanned from the closure
  toplevel's requisites (the `list_groups` walk, now recorded instead
  of discarded), the rev from the store path's `+git<shortRev>`
  version suffix (resolved to a full rev through the Bare when
  present), and the origin tag; never the live config dir or its
  `flake.lock` (see the corpus design decision),
- jobs: `WM_JOB_ID` / `WM_ROOT_FLOW_JOB_ID` / `WM_ROOT_JOB_ID` /
  `WM_FLOW_PATH` from the step's environment (defined in the fork's
  `windmill-common/src/variables.rs`, injected into step processes by
  the worker's reserved-variables path). `WM_ROOT_FLOW_JOB_ID` names
  the INNERMOST root flow, so under flow embedding the outermost run
  is `WM_ROOT_JOB_ID`; record both,
- request: `f/fstests/render_config`'s returned `check_args`,
  `sections`, `armed`, and `skipped` (with reasons) stored verbatim,
  plus the verbatim `sections`/`local_config` form inputs (the return
  alone drops unknown requested names and mixes arm-all skips into
  `skipped`; see the request design decision).

A `publish_results: bool` flow knob (default on) gates the step. The
step must degrade like `collect` does: a crashed or timed-out run
still publishes (the bundle records the failure), a missing artifact
is flagged in the manifest rather than raising.

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
verbatim as `display.json` (the rich display payload), exactly like
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
  `display.json.xz` from the store (stdlib `lzma`) and returns the
  payload unchanged, prepended with
  a `markdown` element summarizing the manifest (suite, kernel, tested
  artifact names, run window, verdict). Because the payload is stored
  verbatim in the rich display shape, the job's result panel renders
  the same tables the original run showed. Read-only, never raises on
  a missing optional artifact (it reports the gap as a row instead).
- New step `f/workbench/compare_results.py`: TWO bundle pickers
  (baseline and candidate, same `list_results` backing, filtered to a
  shared suite), running `results.compare` over the two manifests and
  rendering its output through the verified display contract:
  `render_all` of a `markdown` identity banner leading with the
  corpus/geometry/truncation warnings, one explicit
  `table-row-object` (with a leading header row) per transition
  bucket, the set differences with per-status counts, and the runtime
  swings. Transition tables cap like the report step's test table
  does, failures first, and say what they dropped. The whole return
  must stay inside the 90 kB inline ceiling; the full comparison is
  also data in the result, so nothing is lost when tables cap.
  Comparison is manifest-only by design: it needs no evidence file
  and therefore works on a host that only fetched manifest-bearing
  bundles.
- The qualify verdicts (Phase 6) render through the same render step:
  a verdict bundle's `display.json` is its per-test outcome table.

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
original run's report tables in the Windmill result panel;
`f/workbench/compare_results` over the two retro-validated A/B
bundles reproduces the validation section's buckets (37 common
failures, zero transitions) in the result panel; and
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

## Validated against real runs (2026-08-13)

The schema and comparison semantics above are not speculative; they
were exercised by generating schema-shaped manifests retroactively
from the on-share artifacts of the completed vanilla/writethrough A/B
(section `xfs_full_bs16k_ss16k`, 1277 tests each side) and comparing
them:

- The A/B reproduced exactly: 37 common failures, zero transitions,
  1240 unchanged, matching the hand-run comparison that motivated
  this work. The identity banner correctly led with "corpus identity
  unknown" (retro manifests cannot recover it, which is the point of
  recording it at publish time).
- The truncation semantics held on a real interrupted attempt (99
  tests executed of 1277): the comparison scoped to the 98-test
  intersection, reported the 1179 baseline-only tests as one set
  difference with per-status counts, and produced zero fake
  "coverage lost" transitions.
- The notrun-reason drift bucket surfaced 12 changes that were all
  device-naming noise (`nvme4n1` vs `nvme2n1`), which is what makes
  compare-time device-role canonicalization a requirement rather
  than a nicety.
- Runtime comparison flagged 2x to 10x swings between an attempt that
  ran alone and one that ran while the second guest was mid-suite;
  co-tenancy pollution is why swings stay advisory.
- Cost measurement: the 1277-test per-test map is ~112 kB of plain
  JSON (~6.5 kB compressed), with failure messages the largest
  contributor, hence the 200-character message cap.
- The per-attempt artifact model is stable: the flow's own
  `_rotate_results` (not xfstests) rotates `result.xml`/`check.log`
  into `result.NNNN.xml`/`check.NNNN.log`, each attempt's xunit is
  self-contained (statuses, times, failure messages, notrun reasons,
  and the run window's three timestamps), and the appending
  `check.log` carries the `Interrupted!` marker the xunit lacks.

The same day, the second (matched-corpus) A/B rerun finished and the
comparator ran on it live, which turned into a second validation
round with real findings:

- Zero pass-to-fail transitions again (the writethrough series is
  clean on both corpora), 33 common failures on the new pair.
- The set-difference bucket caught a REAL residual corpus skew the
  rerun was meant to eliminate: `generic/798` was requested only on
  the writethrough side. Chasing it through the closures (the
  manifest's own capture path: the xunit and logs cannot answer this)
  showed vanilla ran `xfstests-2026.03.20+git acb6d4c` (an upstream
  master state, which does not carry `generic/798`/`799` yet) while
  writethrough ran `+git b58c1adc` (the July-locked
  `ojaswinm/iomap-buf-writethrough2`, which carries both). The
  running closures also disagreed with the currently rendered per-VM
  configs (vanilla's override is gone from its config dir but baked
  into its running closure), which is why identity must be captured
  from the closure at publish time, never from config files.
- Same-VM longitudinal comparisons (old corpus to new corpus, same
  kernel) attributed every remaining delta: `generic/453`,
  `generic/454`, `generic/753` fixed and 16 tests' coverage gained
  (`generic/791`, `generic/796`, `xfs/654`-`xfs/667`) identically on
  BOTH guests, and writethrough's corpus was identical across its two
  runs, so those gains are environment (closure) changes, not corpus.
- A coverage finding the buckets forced into view: `generic/799`, the
  new writethrough-isize test, has never run anywhere, because it
  declares `_begin_fstest quick` without `auto`, so `-g auto` runs
  never select it. Requested-set accounting is what makes a
  never-selected test visible at all.

The prototype's parsing and comparison logic is the reference for
Phase 1's `compare` core; the interim operator tool built from it is
`scripts/compare-fstests-runs.py`, which compares two raw results
directories (by rotated attempt or the live files) without needing
bundles, an instance, or a running flow.

## Sequencing and repo rules

Phases 0 to 3 are the core and land in order; 4, 5, 6 are independent
of each other and can land in any order after 3. All work on `main`
(the branch-bound deploy model), scoped `git add` by explicit path,
push-only `wmill sync push` deploys performed by the user, commit rules
per CLAUDE.md (atomic, `subsystem: summary`, Generated-by +
Signed-off-by, `nix flake check` + `check-style.sh` before each
commit).
