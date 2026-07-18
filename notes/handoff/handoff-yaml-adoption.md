# Handoff: adopt the wmill canonical YAML format (push+pull) vs lightweight push-only

**Repo:** `/home/dagomez/src/kdevops-ng`. The work is on branch
**`explore/wmill-canonical-form`** (3 commits ahead of `main`). Re-check
`git log`; concurrent sessions commit here.

## What was settled (read these, do not re-derive)

The whole root-cause investigation and the empirical results are captured in:

- **Memory** `~/.claude/projects/-home-dagomez-src-kdevops-ng/memory/wmill-sync-push-only.md`
  (authoritative): why `sync pull` reserializes (yamlOptions `sortMapEntries` +
  `singleQuote`, `cli/src/commands/sync/sync.ts:500-525`); that the loss is
  **cosmetic, not semantic** (verified by replaying the serializer: zero data
  diff); that it is **not a yaml-version issue** (CLI bundles `yaml` 2.8.2, same
  family); that the literal-vs-folded choice is pure **line length** (a
  description stays a clean literal `|` block iff every line is `<= 80` cols,
  because `limit = lineWidth(80) - indent`); and that `wmill dev` is the gentle
  UI round-trip path.
- **Commits on the branch** (the diffs ARE the artifact; reference, don't copy):
  - `8fe2dbe wmill: adopt canonical sync-pull yaml form for evaluation`
  - `9c225f2 wmill: reflow descriptions to clean literal blocks`
  - `8dc96ce build: enforce wmill description reflow`
- **Tooling:** `scripts/reflow-descriptions.py` (`--check` in
  `scripts/check-generated.sh`; `make reflow` = `--write`).

Validated facts: with the canonical form + reflow on disk, `wmill sync push
--dry-run` reports **0 changes** (git and the live instance fully reconciled),
`make style` is green, `gen-bringup --check` passes. The instance currently
holds this state.

## Focus for the next session

Decide and document the two supported workflows, then land the chosen default.

**Mode A: full bidirectional (push + pull).** Adopt the canonical YAML as the
source-of-truth format (the branch). Users edit in the Windmill UI or CLI, run
`wmill sync pull`, `make reflow` (keeps descriptions as clean literal blocks),
`wmill sync push`, commit. Round-trip is clean (0-diff). This unlocks full wmill
features (UI editing, `wmill dev`, real pull). Cost: descriptions are
machine-wrapped, schema keys alphabetized, inline flow scripts externalized
(already handled, gen-bringup regenerates fine).

**Mode B: lightweight push-only.** The prior workflow: hand-authored YAML is the
source of truth, `wmill sync push --yes` to deploy, **never pull**. Preserves
hand-tuned formatting; UI edits are not captured back. This is what the memory
originally documented. Some contributors may prefer it.

The user wants **both documented** so a contributor can choose, with Mode A as
the path to "fully fledged wmill features."

## Tasks to land adoption (Mode A as default)

1. **Merge decision:** fast-forward/merge `explore/wmill-canonical-form` into
   `main` (or keep exploring). Big diff; coordinate with concurrent sessions.
2. **Fix `BOOTSTRAP.md` §4 "Daily loop":** it predates this and is now wrong for
   both modes. For Mode A the loop is pull -> `make reflow` -> push -> commit;
   document Mode B as the alternative. (This was a known pending fix.)
3. **Write `docs/wmill.rst`** (was deferred pending this decision): a "Working
   with wmill" page covering both modes, the reflow step, `wmill dev`, and the
   generated-file caveats (`bringup.flow`). Follow the docs conventions
   (SPDX line 1, 80-col, sentence-case headings; `make docs` to build).
4. **ADR** under `docs/adr/` recording the format decision and the two modes.
5. Cross-check the **Python toolkit handoff**: confirm `wmill sync` does not
   reformat `.py` step scripts (it reformats YAML; verify the `.py` path) so a
   Python format pass and Mode A do not fight.

## Open questions

- Default mode for new contributors: A or B? (User leans A for full features,
  but wants B available.)
- Does `make reflow` belong inside a `wmill`-wrapper target (e.g. `make pull`
  that runs `wmill sync pull && make reflow`) so the daily loop is one command?
- Should `bringup.flow` (generated, PyYAML format, never pulled) eventually be
  emitted in wmill-canonical form for uniformity, or stay as-is? (Currently
  exempt from reflow; see `scripts/reflow-descriptions.py` `GENERATED`.)

## Suggested skills

- `cli-commands` (driving `wmill sync`/`dev` during any verification).
- `write-flow` / `preview` (if testing UI round-trips).
- Built-in docs build via `make docs` (Nix-pinned Sphinx).
