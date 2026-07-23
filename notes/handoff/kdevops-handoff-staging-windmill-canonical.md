# Handoff: staging deploy model → move toward Windmill-canonical operation

**Date:** 2026-07-23  **Repo:** `~/src/kdevops-ng` (branch `main`)
**Focus for the next session:** understand the current custom staging/prune
deploy model and its flows, then plan a refactor toward Windmill's *native*
model (workspace forks + git sync, and AI Sessions) so we leverage Windmill
features instead of fighting them. A specific open question: does the `wmill`
CLI support forks/Sessions, or are they UI-only? Investigate with the sources
at `~/src/windmill-labs/`.

Do not re-read the whole prior conversation. This doc + the referenced
artifacts are enough to continue.

---

## 1. What exists now: the custom staging model (read these first)

Authoritative artifacts (do **not** duplicate; read them):
- **ADR:** `notes/adr/0012-staging-workspace-interim.md` — the model, why the
  native model is deferred, the considered options, and the migration plan.
  This is the single most important read.
- **Guide:** `docs/getting-started/wmill.rst` — deploy commands + the "Two
  workflows" (bidirectional vs push-only) + previewing.
- **`CLAUDE.md`** Layout section — the one-paragraph deploy rule.
- **Memory** `wmill-sync-push-only` — updated this session with the deploy-app
  model and the mirror finding (see §2).

The mechanism, in brief:
- **Two Windmill workspaces**, both on branch `main`, both at
  `localhost:8002` (`wmill.yaml` `workspaces:`): `kdevops` = production
  (promoted work only), `staging` = work in progress.
- **Deploy through nix apps, not bare `wmill sync push`:**
  - `nix run .#deploy-staging` → pushes the **whole working tree** to `staging`.
  - `nix run .#deploy-kdevops` → copies the tree to a tmp dir, runs
    `stagingOnlyPrune`, then `wmill sync push --workspace kdevops
    --skip-branch-validation`.
- **`stagingOnlyPrune`** (in `nix/apps/default.nix`) removes the staging-only
  set from the kdevops push: `f/kunit`, `f/selftests`, `f/runtime_tests`,
  `f/usertests`, `f/kernel/bisect*`, `check_usertests`. **Promote a suite = drop
  its line.**
- Other relevant `nix run .#` shortcuts (all in `nix/apps/default.nix`):
  `format`, `reflow`, `preview-smoke`, `docs`, `serve`, plus the
  `windmill-*`/`monitoring-*` lifecycle apps. Run `nix flake show` to list.

### Commits that introduced the staging features (other session)
- `2fa576e` wmill: add a staging workspace to gate promotion  (`wmill.yaml`)
- `18d8189` nix: add the staging and kdevops deploy apps      (`nix/apps/default.nix`)
- `b33e550` adr: record the staging workspace as a Windmill-forks interim
- `26ea19f` docs: route workspace deploys through the deploy apps (`CLAUDE.md`)
- `465a3bc` docs: route the wmill guide's deploys through the apps (`wmill.rst`)

---

## 2. The limitation that motivates the refactor (verified this session)

**`wmill sync push` MIRRORS** — it deletes remote items that are absent locally.
Verified by dry-run: prune `f/fstests` locally, dry-run push to `kdevops`, and it
reports `164 changes to apply` with `- script f/fstests/collect.py`, `- script
f/fstests/common.py`, … (the `-`/red = delete). Repro:
```
tmp=$(mktemp -d); cp wmill.yaml f -r "$tmp"; rm -rf "$tmp/f/fstests"
(cd "$tmp" && wmill sync push --workspace kdevops --skip-branch-validation --dry-run)
```
Consequence: **the prune gate is all-or-nothing per suite.**
- A *new* suite: prune → mirror keeps it out of prod (the case the model was
  built for).
- A *promoted* suite (e.g. `f/fstests`): you **cannot** stage a refactor to it.
  Not pruning mirrors `main` (refactor and all) to prod; pruning **deletes** the
  suite from prod. There is no "staging version vs prod version," because both
  deploy apps mirror the same `main` tree and the only knob is include/exclude a
  whole file set.

So the model isolates whole **new workflows**, not **changes to
production-ready ones**. For a promoted suite, *committing to `main` is
promoting* (the next `deploy-kdevops` mirrors it, and a first-time user's
`deploy-kdevops` gets it too).

### The live example (do not lose this thread)
The fstests **per-section-env** refactor is the concrete instance that surfaced
this. It is committed to `main` and deployed to `staging`, but **not** promoted
to `kdevops`:
- `12874c8` testSuites: read each section's own env file  (vendored unit:
  `EnvironmentFile=-${stateDir}/%i.env`)
- `04a4077` fstests: give each section its own env file  (the `f/` half)

It is a **coordinated `f/` + closure change**: `render_config` now writes
`<section>.env` and `prepare` no longer writes `local.config`, which only works
with the vendored unit's per-instance `EnvironmentFile`. Pushing the `f/` half to
prod without the rebuilt closure would break an **existing** prod guest's fstests
(old unit still reads `check.env`); a brand-new setup is fine (fresh closure).
**Pending decision:** revert `12874c8`+`04a4077` from `main` and re-stage from
the working tree (recommended: keeps `main` prod-safe), OR hold `deploy-kdevops`
until the closure is rolled to prod guests together. Two other this-session
commits are independent and safe to keep: `3eee6e2` (reflow fix) and the earlier
rtinherit commit.

---

## 3. What to explore instead (the actual task)

ADR 0012 names the target and treats `staging` as a deliberate interim: **the
`staging` workspace is exactly the Community-Edition two-workspace fork slot a
Windmill fork would occupy** (freeing it is one workspace deletion). The native
model is **workspace forks + git sync** (Windmill "Stage 2 → Stage 3"), and **AI
Sessions** build directly on forks (a session develops in an isolated fork and
merges back through review). Forks give the *per-change* isolation the prune
gate cannot, which is the fix for the refactor-to-promoted-suite gap in §2.

### The open question to investigate (user's explicit ask)
The user recalls a limitation that the `wmill` CLI/tooling did **not** support
forking (and that forks/Sessions are UI-only) but is not sure. **Verify against
the sources:**
- `~/src/windmill-labs/windmill/cli` — the `wmill` CLI (TypeScript). Grep for
  `fork`, `session`, `branch`, `workspace`; determine whether a fork can be
  created/synced/merged from the CLI (scriptable into a nix app), or only via
  the UI/API.
- `~/src/windmill-labs/windmill/docs` and
  `~/src/windmill-labs/windmilldocs-public/docs` — the fork / git-sync / Sessions
  documentation.
- Our Windmill fork is **1.741** (per ADR); AI Sessions landed upstream *after*
  it, so a **version bump** is likely required. Confirm: Sessions availability,
  Community-Edition fork limits (ADR states CE = one fork at a time; parallel
  forks = Enterprise), and whether git sync's **bi-directional** nature is
  compatible with (or should revise) the repo's git-is-truth doctrine (ADR flags
  this "center of gravity" shift as deserving its own decision).

### Deliverables the next session should produce
1. A findings note: forks/Sessions CLI-scriptable vs UI-only, the version bump
   needed, and the CE constraints — enough to decide go/no-go on the native model.
2. If viable: a migration sketch that follows ADR 0012's "Migration" section
   (bump the fork, confirm CE fit, delete `staging` to free the slot, enable git
   sync on `kdevops`, move WIP isolation from the prune gate to forks; retire
   `deploy-staging`/`deploy-kdevops` and `stagingOnlyPrune`).
3. A recommendation on the per-section-env change (§2) under whichever model is
   chosen.

---

## 4. Cross-cutting context / gotchas

- **Bidirectional pull is now RECOMMENDED** (reversing the old "never pull"):
  the canonical form + reflow make `wmill sync pull` round-trip diff-free
  (`|` block scalars store the value's newlines). See wmill.rst "Two workflows".
- **Reflow was fixed this session** (`3eee6e2`, `Fixes: 8dc96ce7f139`):
  `nix run .#reflow` now rewraps only *overflowing* descriptions (it used to
  greedy-repack all of them, churning 40+ files). Scoped reflow is safe now.
- **Concurrent sessions edit this repo.** Stage only your own files by explicit
  path; never bulk-add (memory `scoped-git-staging`). The other session owns the
  staging/deploy work and `docs/deployment/*`, `docs/getting-started/wmill.rst`,
  `CLAUDE.md`.
- **Work on `main`.** A long-lived non-`main` branch + `nix format`/`reflow`
  historically injects a junk `wmill.yaml` workspace (memory
  `qsu-bringup-store-reuse`; ADR 0012 cites this as a reason it rejected a manual
  staging branch). Note this when evaluating any branch/fork-per-change workflow.
- **Commit rules** (`CLAUDE.md`): `subsystem: summary` ≤75 imperative;
  plain-English body wrapped 75; `Generated-by: Claude AI` immediately before
  `Signed-off-by: Daniel Gomez <da.gomez@kernel.org>`; run `nix flake check` +
  `nix develop .#checks --command bash scripts/check-style.sh` before committing.

---

## 5. Suggested skills / agents for the next session

- **`/handoff`** produced this doc; nothing else to invoke to start.
- **Explore agent** (read-only, broad) over `~/src/windmill-labs/` for the
  fork/Sessions/git-sync investigation — it is a large multi-repo sweep where you
  only want the conclusions.
- **`typescript-expert` agent** for a deep read of the `wmill` CLI
  (`~/src/windmill-labs/windmill/cli`, TypeScript) to answer "CLI-scriptable vs
  UI-only" precisely.
- **`python-expert` agent** if/when implementing the per-section-env re-stage or
  any `f/` deploy-tooling change (the project's convention is to delegate `f/`
  and `scripts/` refactors, spec tightly, then review + gate yourself).

## 6. Reference index (paths / commits — do not duplicate their content)

- ADR: `notes/adr/0012-staging-workspace-interim.md`
- Guide: `docs/getting-started/wmill.rst`  ·  `CLAUDE.md` (Layout)
- Apps: `nix/apps/default.nix` (`deploy-staging`, `deploy-kdevops`,
  `stagingOnlyPrune`)  ·  `wmill.yaml` (workspaces)
- Windmill sources: `~/src/windmill-labs/windmill/{cli,docs}`,
  `~/src/windmill-labs/windmilldocs-public/docs`
- Memory: `wmill-sync-push-only` (updated), `fstests-rt-file-placement`,
  `deferred-cleanup-sweeps`
- Staging commits: `2fa576e`, `18d8189`, `b33e550`, `26ea19f`, `465a3bc`
- Live-example commits: `12874c8`, `04a4077` (per-section-env, not promoted);
  `3eee6e2` (reflow fix)
