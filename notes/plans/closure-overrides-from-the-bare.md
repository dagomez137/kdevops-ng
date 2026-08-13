# Plan: closure package overrides through the Bare

Working note, 2026-08-13. The mission: implement ADR 0014
(`notes/adr/0014-closure-overrides-from-the-bare.md`): the closure's five
overridable packages (fio, xfstests, xfsprogs, libbpf-tools, blktests) get
the kernel-style worktree treatment: ref pickers off the project's Bare,
`file://` Bare-backed `<pkg>-src` flake inputs, and later the developer
worktree tail and b4 series support.

## Verified mechanism (2026-08-13, live host)

- `nix flake prefetch "git+file://$SYSTEM_DIR/bare/fio.git?ref=refs/remotes/mirror/master"`
  fetches and locks to a concrete rev; same for `refs/tags/fio-3.42` and a
  rev-only mid-history sha. Alternates (bare borrows mirror objects) are no
  obstacle.
- A package Bare carries `refs/tags/*` and `refs/remotes/mirror/*` only;
  `refs/heads/*` stays empty until a developer pushes (fetch.py reserves
  it). `f.common.gitrefs.list_refs` reads only heads and tags today, so a
  fresh package Bare offers tags alone; the mirror branches must be added.
- bcc `.gitmodules` names absolute GitHub URLs (libbpf, bpftool, blazesym):
  `submodules = true` works with a `file://` main URL, fetching submodules
  from upstream, the same network dependency as today.
- The vendored nixos-flake consumes no `<pkg>-src` input anywhere; the
  declaration is wholly owned by `f/nix/render_config.py`. No vendor change.

## Phase 1: map, pickers, Bare-backed inputs

### `f/common/gitrefs.py`

- `_read_refs` also collects `refs/remotes/mirror/*` (packed and loose) as
  a third kind; `list_refs` emits them as `mirror/<branch>` rows between
  the developer branches and the tags. Kernel and QEMU pickers gain the
  same rows (intended, per the ADR).
- New `qualify_ref(repo: str, ref: str) -> str | None`: fully qualified
  refname in the worktree resolution order: `refs/tags/<v>`, then
  `refs/remotes/mirror/<v>`, then `refs/heads/<v>`, then
  `refs/remotes/<v>` (covers picker values `mirror/x` and typed peer
  branches). Same file-read universe as the picker, no git subprocess.
- Fixture tests beside the existing gitrefs tests: fake bare with
  packed-refs plus loose refs covering all namespaces; ordering; the
  qualification order including a tag/branch name collision.

### `f/nix/render_config.py`

- `_PKG_PROJECTS = {"fio": "fio", "xfstests": "xfstests-dev",
  "xfsprogs": "xfsprogs-dev", "libbpf-tools": "bcc",
  "blktests": "blktests"}` beside `_OVERRIDABLE_PKGS`.
- `_source_overrides_to_list()` reads `ref` (not `src`) per package; blank
  keeps the pinned version; a row becomes
  `{pkg, project, ref}` (+ `attrs` from `_PKG_SOURCE_ATTRS`).
- `_override_input()` branches: a row with `project` renders
  `type = "git"; url = "file://<SYSTEM_DIR>/bare/<project>.git"` with
  `ref = "<qualified>"` (via `gitrefs.qualify_ref`) or `rev = "<sha>"`
  for a full 40-hex value, plus `submodules = true; flake = false;`.
  A row with `src` (from `extra_overrides`) keeps today's rendering.
- Fail fast: missing Bare -> FileNotFoundError naming the path and
  `f/workbench/init`; unresolvable ref -> ValueError naming the tried
  namespaces; reject a duplicate pkg across curated + extra rows (today
  that renders two same-name inputs and Nix errors late).
- `f/nix/render_config.script.yaml`: per-package sub-schema becomes one
  `ref` string ("branch, tag or commit in the Bare; blank keeps the
  pinned version"); the flow owns the picker/toggle UI.
- Tests: extend `tests/test_nix_render_config.py` (env fixture gains a
  fake `SYSTEM_DIR/bare/<project>.git` with packed-refs); curated row
  rendering (ref, tag, rev forms), blank-keeps-pinned, error paths,
  extra_overrides unchanged, duplicate-pkg rejection.
  `tests/test_nix_lock_config.py` needs nothing (regex already matches).

### `f/nix/build.flow/flow.yaml`

- Each package sub-object in `source_overrides` becomes the kernel triple:
  `ref` (type object, `format: dynselect-list_<pkg>_refs`, no default,
  `showExpr: fields.custom_ref !== true`), `custom_ref` (boolean), and
  `git_ref` (string, shown when the toggle is on). Titles keep the package
  name; descriptions follow the kernel `ref`/`custom_ref` wording.
- Schema-root `x-windmill-dyn-select-code` (the flow's first) defines the
  five helpers, each `list_refs("<project>", filterText)` in the standard
  try/except wrapper.
- The `render_config` transform reconciles generically:
  `Object.fromEntries(Object.entries(flow_input.closure?.source_overrides
  ?? {}).map(([p, o]) => [p, {ref: o?.custom_ref ? (o?.git_ref ?? "") :
  (o?.ref || "")}]))`, keeping the step signature free of UI toggles.
- Risk: `showExpr` with `fields.<sibling>` at this nesting depth
  (group > source_overrides > package) is unproven, deeper still in
  bringup's embed. Verify in the dev page preview after deploy-staging;
  the fallback is dropping the toggle and keeping `ref` + an always-shown
  `git_ref` ("replaces the picked ref when set").

### `scripts/gen-bringup.py`

- Add the five `list_<pkg>_refs` helpers to `DYN_SELECT_CODE` (an embedded
  picker's helper does not travel with the schema), then regenerate:
  `python3 scripts/gen-bringup.py`. The closure group's object-spread
  transform passes the new fields through untouched.

### Commit sequence (each gated by `nix flake check` + check-style)

1. `notes: record ADR 0014 and the implementation plan`
2. `gitrefs: list mirror branches and qualify refs for fetchers`
3. `nix: build closure package overrides from the Bare` (render_config +
   script.yaml + tests)
4. `nix: give closure overrides kernel-style ref pickers` (build.flow +
   gen-bringup.py + regenerated bringup.flow)
5. `docs: document the closure override pickers` (below)

Deploy with `nix run .#deploy-staging`; verify pickers in the dev page
(nix build form, then bringup's embedded form); a staging bringup run with
an xfstests `mirror/for-next` override is the end-to-end check, and a bcc
override exercises the submodule fetch.

### Docs (staged drafts, same commit 5)

- `docs/flows/nix-build.rst`: add the schema/override reference section
  modeled on kernel-build.rst's Worktree section (picker entry + gated
  custom-ref entry, resolution order); touch the four passing mentions of
  free-form overrides (lines ~29, ~65, ~117, ~153).
- `docs/flows/workbench-init.rst`: name the closure build as a consumer of
  the mirrored package trees.
- `docs/flows/guests.rst`: the blkalgn bcc-fork line now points at the
  picker.

## Phase 2: developer worktree tail

- `f/nix/build.flow` gains `deploy_developer_worktree`, `custom_group`,
  `worktree_group`, `recreate_developer_worktree` (one shared set for the
  flow, aligned with the kernel/qemu naming) and a `deploy_worktree` step
  calling `f/workbench/worktree/init` with one `{project, git_ref}` row
  per active override, `skip_if` no override is active or the toggle is
  off. `prepare_developer` resolves the same picker values through the
  same order, so no new resolution code.
- No fetch_devel equivalent: the packages have no devel layer; the tail
  only lays or refreshes the group checkout at the built ref.
- Auto group naming has no build label to derive from; default `vanilla`
  unless `custom_group` names a topic.

## Phase 3: b4 series per package

- Per-package `b4_series` field; `f.common.worktree.prepare()` lays the
  worker worktree, applies the series, publishes `b4/<slug>` to the Bare;
  the flake input then refs the published branch (`refs/heads/b4/<slug>`
  qualifies through the existing order). Needs a step before
  `render_config` in the flow to run `prepare()` per seriesed package and
  hand the branch to the transform. The Bare stays the one channel.

## Out of scope, recorded

- OPEN A (shares single-sourcing) and OPEN B (closure as Store artifact)
  from the closure roadmap memory stay separate.
- The xfstests overlay's carried patches (`patch -p1` in `patchPhase`)
  fail on a fork that already contains those hunks; pre-existing override
  behaviour, unchanged by the Bare form.
- Saved inputs carrying the old `src` fields stop applying (ADR 0014
  consequence).
