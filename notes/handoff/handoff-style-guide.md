# Handoff: formalize a contributor style/format guide (YAML, Python, expandable)

**Repo:** `/home/dagomez/src/kdevops-ng`. Re-check `git log`; concurrent
sessions commit here.

## Why (the user's framing)

Today the project's format rules are real but **scattered and agent-facing**,
not a single human contributor guide. The kernel solves this with
`Documentation/process/` (coding-style, submitting-patches) plus
`scripts/checkpatch.pl` as the mechanical enforcer. kdevops-ng has the pieces
but no consolidated, discoverable document. Without one, contributors will not
converge on a style and the codebase fragments. Goal: a formal guide where every
rule is stated, justified, and tied to the check that enforces it, so there is
"no discussion" about how to produce code, and it is expandable as new languages
/ concerns appear.

## What already exists (consolidate, do not reinvent)

- **`CLAUDE.md`** (agent instructions, checked in): long-form flags rule,
  no em/en-dash appositives, unified Nix CLI rule, Windmill flow/step naming
  (subsystem dirs, verb steps, thin flows), comment discipline, the
  "curated forms" rule, canonical upstream vocabulary, and the **six commit
  rules**. This is the richest source but it is written for the agent, not
  contributors.
- **Enforcement (the "checkpatch" analog):**
  - `make style` -> `scripts/check-style.sh`: trailing whitespace, EOF newline,
    HEAD commit trailers (`Generated-by`/`Signed-off-by`), RST 80-col, RST SPDX
    on line 1. Scopes out `f/`, `vendor/`, `LICENSES/`, `wmill-lock.yaml`,
    `screenshots/`, vendored `get_maintainer.pl`.
  - `make generated` -> `scripts/check-generated.sh`: drift guards
    (`gen-bringup.py --check`, `reflow-descriptions.py --check`).
  - `make reflow`: rewrap wmill descriptions (see the YAML adoption handoff).
- **Editor config:** `.editorconfig` (charset/EOL/indent per type; RST+MD 80,
  Rust 100), `.helix/languages.toml` (text-width, rulers, formatters: rustfmt,
  black; pyright LSP).
- **YAML findings + workflow:** memory `wmill-sync-push-only.md` and
  `scripts/reflow-descriptions.py` (the canonical wmill format + the
  <=80-col-per-description-line invariant).

## Focus for the next session

Design and draft a formal style guide as part of the Sphinx docs site
(`docs/`, already built via `make docs`, served via `make serve`). Proposed
shape (RST, expandable):

- `docs/contributing/index.rst` (overview + how rules are enforced).
- `docs/contributing/commits.rst` (lift the six commit rules verbatim-equivalent
  from `CLAUDE.md`).
- `docs/contributing/style-yaml.rst` (the wmill canonical format, the two
  push/pull modes, the <=80 description-line invariant, `make reflow`, generated
  files; depends on the **YAML adoption handoff** outcome).
- `docs/contributing/style-python.rst` (the toolkit, line length, ruff/mypy
  config, Windmill `main()` typing caveat; depends on the **Python toolkit
  handoff** outcome).
- `docs/contributing/style-rst.rst` (the existing RST rules: SPDX line 1,
  80-col, kernel heading-adornment order, sentence case).
- `docs/contributing/enforcement.rst` (the checkpatch analog: a table mapping
  each rule -> the `make` target / check that enforces it -> how to fix).

Each rule entry should carry: statement, rationale, the enforcing check, and the
fix command. That is what removes ambiguity.

## Key design decisions

1. **Single source of truth.** Decide the relationship between `CLAUDE.md` and
   the new docs. Recommended: the `docs/contributing/` guide is canonical for
   humans; `CLAUDE.md` stays the agent-facing distillation and **links** to it,
   so rules are not duplicated and cannot drift. Or generate one from the other.
2. **Enforcement-first.** Prefer rules that a check enforces. Where a rule is
   currently only prose (e.g. no em-dash, long-flag, nix-CLI), decide whether to
   add a mechanical check (extend `check-style.sh`) or keep it advisory. The
   no-em-dash and nix-CLI rules already have grep-able audits in `CLAUDE.md`;
   those could become `check-style.sh` checks (a real "checkpatch" step).
3. **Expandability.** Structure so adding a language (e.g. Nix, shell, Rust)
   later is "add a page + a check," not a rewrite.
4. **Scope/exemptions** must be stated explicitly (generated `f/`, vendored
   trees, license texts), mirroring `check-style.sh`'s scope array.

## Dependencies / sequencing

- The YAML page needs the **YAML adoption** decision (Mode A/B) first.
- The Python page needs the **Python toolkit** decision (tool, line length,
  scope) first.
- The RST page and commit-rules page can be written now (rules already settled).
- Consider doing `enforcement.rst` last, once the YAML/Python checks exist, so
  the rule->check table is accurate.

## Pointers

- Docs conventions enforced today: `scripts/check-style.sh` (RST 80-col, SPDX).
- Build: `make docs` (Nix-pinned Sphinx via `vendor/nixos-flake#docs`);
  `make serve` to view over an SSH tunnel.
- Kernel analogs to model tone/structure on:
  `Documentation/process/coding-style.rst`,
  `Documentation/process/submitting-patches.rst`, `scripts/checkpatch.pl`.

## Suggested skills

- Docs build via `make docs`; `jylhis-skills-core:humanizer` for prose polish.
- `jylhis-skills-core:grill-with-docs` (stress-test the guide against the
  existing model/terminology before committing).
- Reference the other two handoffs (Python toolkit, YAML adoption) as inputs.
