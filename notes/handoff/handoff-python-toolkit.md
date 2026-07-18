# Handoff: Python lint/format/type toolkit for kdevops-ng

**Repo:** `/home/dagomez/src/kdevops-ng` (branch work is on
`explore/wmill-canonical-form`; `main` is the nix-first rebuild). Re-check
`git log` (concurrent sessions commit here).

## Focus for the next session

Decide and stand up a Python toolkit so all Python in the repo is "pristine":
zero lint warnings, consistent formatting, type-checked, following canonical
upstream best practices. Then wire it into `make` and the Nix devshell (and CI
when one exists). This is an **exploration + proposal** first; agree the choices
before mass-reformatting 60 files.

## Current state (do not re-derive)

- **Two kinds of Python:**
  - `scripts/*.py` (2 files): `gen-bringup.py`, `reflow-descriptions.py`
    (added in commit `8dc96ce`). Plain CPython + PyYAML. These are repo tooling.
  - `f/**/*.py` (58 files): Windmill **step scripts** (e.g. `f/kernel/*.py`,
    `f/fstests/*.py`, `f/common/*.py`). These are hand-authored workspace content
    that wmill manages. **Critical:** a step's `def main(...)` parameter type
    annotations are *semantic* in Windmill: they generate the UI form schema. So
    a type checker / linter must not "simplify" them, and `main` signatures use
    domain types and sometimes `any`. Imports look like `from f.common import
    store`; there are Windmill runtime globals (`wmill` client) a linter won't
    resolve without config.
- **No toolchain configured yet:** no `pyproject.toml`/`ruff.toml`/`.flake8`,
  **no `.github/workflows`** (no CI at all yet).
- **What exists:** `.editorconfig` sets `[*.py] indent 4`. `.helix/languages.toml`
  runs `black` (auto-format) + `pyright` LSP for Python locally (per-developer,
  not enforced). `make style` (`scripts/check-style.sh`) checks only trailing
  whitespace / EOF newline / RST / commit trailers, **not** Python lint/format.
- **Toolchain delivery is Nix-first** (north-star memory
  `project-rebuild-nix-first`): tools should come from `vendor/nixos-flake`
  devshells (`make docs` already uses `nix develop ./vendor/nixos-flake#docs`).
  Add Python tools to a devshell, do not assume host-global installs.

## Key decisions to make

1. **Tool choice.** Recommend evaluating **ruff** (single fast tool: lint +
   format + import-sort, replaces black/isort/flake8/pyupgrade) vs the
   black+flake8+isort stack already half-present (helix uses black). Ruff is the
   current canonical upstream choice; black-compatible formatter. Decide one.
2. **Type checking.** `pyright` (already in helix) vs `mypy`. Decide strictness.
   Crucial sub-question: **do we type-check `f/` step scripts at all?** Their
   `main()` params are Windmill-schema-bearing and often `any`; a naive
   `disallow_untyped` would fight Windmill semantics. Options: lint `f/` but skip
   strict typing there; or a Windmill-aware stub. Resolve before enabling.
3. **Line length.** Repo uses 80 for docs/RST, 100 for Rust. Python has no rule
   yet (black defaults to 88). Pick one and set it everywhere (ruff line-length,
   editorconfig `[*.py] max_line_length`, helix `text-width`). Note `f/` step
   code is wmill-managed; confirm wmill's push/pull does not reformat `.py` (it
   stores them; check whether it touches indentation/quotes like it does YAML).
4. **Scope.** `scripts/` definitely. `f/` step scripts: lint yes, but the rules
   must tolerate Windmill patterns (unresolved `wmill` import, `main` signature,
   `from f.x import y`). Provide ruff `per-file-ignores` / `extend-exclude` as
   needed. Decide whether wmill-generated portions are exempt (analogous to how
   YAML `f/` is style-exempt in `check-style.sh`).
5. **Enforcement + UX.** Add `make lint` / `make format` targets and a
   `--check`-style gate folded into `make style` (mirror the existing
   `check-generated.sh` drift-guard pattern). Goal: `make style` fails on any
   Python warning. Add the tools to a Nix devshell so `make lint` is hermetic.
   When CI is introduced, run the same `make` targets.

## Consequences to weigh

- Mass-reformatting 58 `f/` files is a large diff and interacts with the YAML
  adoption work (see the YAML adoption handoff): if `f/` is being treated as
  wmill-canonical source of truth, confirm wmill round-trips `.py` unchanged so a
  format pass does not fight a later `wmill sync pull`.
- Picking line length / formatter now avoids churn later; picking wrong means a
  second reformat.

## Pointers

- Repo tooling pattern to mirror: `Makefile` (`style`/`generated`/`reflow`),
  `scripts/check-style.sh`, `scripts/check-generated.sh`.
- Nix devshell to extend: `vendor/nixos-flake/flake.nix` (search `python3` /
  `withPackages`; `make docs` shows the devshell invocation pattern).
- Conventions doc to update once decided: `CLAUDE.md` (and the planned
  formalized style guide, see the style-guide handoff).

## Deliverable

A short proposal (tool, config, scope, line length, enforcement, devshell wiring)
plus, if approved, the config files (`pyproject.toml`/`ruff.toml`), `make`
targets, devshell entry, and the first reformat commit(s). Keep commits atomic
(tooling separate from the bulk reformat).

## Suggested skills

- `agents-language-specialists:python-expert` (idiomatic Python, ruff/mypy).
- `nix` (devshell integration in `vendor/nixos-flake`).
- `devops-skills:makefile-generator` / shell skills (the `make` + check wiring).
- `update-config` only if editor/settings changes are needed.
