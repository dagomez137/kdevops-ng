# kdevops-ng

A from-scratch project that self-hosts a Windmill instance and manages its
workspace content (scripts, flows, apps, resources, triggers) as code in git.
The instance runs locally and is reachable only on `127.0.0.1:8000`
(SSH-forward to use the UI). `wmill sync pull` / `wmill sync push` move
workspace content between the instance and this repository; git is the source
of truth. The repository name predates a fuller description that will be added
later.

## Layout

`BOOTSTRAP.md` is the minimal end-to-end setup. `deploy/` holds the instance
deployment methods. `nix/` is the current default; `podman/` is retired and
`distro/` is planned. See
`deploy/README.md`. `wmill.yaml` is the workspace-as-code configuration (code
and resources, no secrets, single `kdevops` workspace on branch `main`). `f/`
holds the workspace content and is machine-managed by `wmill`. The root
`flake.nix` and `scripts/` provide the checks, run with `nix flake check` (see
`docs/contributing/development.rst`).

## Conventions

Always use long-form command flags everywhere (scripts, docs, examples): for
example `mkdir --parents`, `rm --recursive --force`,
`podman build --tag --file`, `apt install --yes`, `npm install --global`,
`qemu-img create --format qcow2`. Use a short flag only when no long form
exists, such as `ssh -L`.

Avoid the em-dash and en-dash appositive style in all prose, including
documentation, code comments, and commit messages. Rewrite such constructions
as separate sentences, or use a colon, semicolon, or parentheses instead.
Box-drawing connectors in a genuine diagram are fine; this rule is about em and
en dashes inside sentences.

Distinguish a tool's project name from its command in all prose (docs, code
comments, commit messages). Name the project in plain text with its canonical
spelling (Ruff, Pyright, Black, PyYAML, QEMU, NixOS; lowercase names keep their
case, such as isort and pyflakes) and backtick the executable you run (`ruff`,
`pyright`, `nixfmt`). So "Ruff is the formatter", but "run `ruff check`".

Always use the modern unified Nix CLI (`nix <subcommand>`) everywhere, in code,
scripts, docs and "Equivalent command" lines, and never the classic `nix-*`
binaries. Use `nix build <path> --out-link <link>` to create a GC root (not
`nix-store --add-root --realise`), `nix store add-path` to add a tree, `nix
store gc` to collect garbage (not `nix-collect-garbage`), `nix develop` for a
dev shell (not `nix-shell`), `nix build`/`nix path-info` (not
`nix-build`/`nix-instantiate`/`nix-env`). The unified CLI is gated behind the
`nix-command flakes` experimental features, which `f/common/devshell`'s `Nix`
runner already enables, so route store/build commands through it. Audit with
`grep -rnE 'nix-store|nix-build|nix-instantiate|nix-env|nix-shell|nix-collect-garbage' f/ scripts/ docs/`;
a match outside a filename or upstream proper noun is a regression.

When writing or extending Windmill flows and steps, also follow these rules:

- **Subsystem dirs, verb steps, thin flows.** A subsystem directory
  (`f/kernel`, `f/qemu`, `f/qsu`, `f/nix`, `f/fstests`, `f/common`,
  `f/workbench`) groups one concern. A step is a `.py` script named for the
  action it performs in the imperative mood (`build`, `configure`, `compile`,
  `install`, `boot`, `publish`, `fetch`), in `verb_object` snake_case when it
  takes an object (`prepare_worktree`, `fetch_identity`, `install_modules`,
  `reuse_check`). A flow is `<verb>.flow` that composes steps (`build.flow`,
  `boot.flow`, `bringup.flow`). Shared libraries and data modules are nouns,
  not steps (`common.py`, `identity.py`, `worktree.py`, `store.py`,
  `devshell.py`). Keep one step per concern and let thin flows compose them.
- **Terse comments, only when needed.** Add a comment only when what the code
  does is not obvious. The "why" belongs in the commit body, not inline.
- **Reuse the shared dispatcher.** Run external commands through the
  `f/common/devshell` runners (`DevShell`/`Systemd`/`Nix`/`run_logged`); extend
  that module rather than fork it. Add a new dispatcher only when the work does
  not fit `devshell` semantically. Never use bare
  `subprocess.run`/`os.system`/`shell=True`.
- **Compose commands explicitly; never bury execution.** Build an argv list
  (no shell strings) and let the runner print the exact, copy-pasteable
  invocation before it runs. Surface filesystem mutations in the job log too.
  Print `wrote <path>` / `copied <src> -> <dest>`, matching `f/kernel`'s
  "artifact ready" style. Nothing silent.
- **Don't restate what the runner logs; log it once, at the source.** The runner
  already prints the exact command, so never hand-write a second copy of it. A
  mirrored string drifts from what actually executes and quietly lies. A
  hand-written log line should carry only what the runner's output lacks (a
  baseline value, a decision, a count), as data, never a paraphrase of the
  command. The same applies to any value with one true source: derive or print
  it from that source, don't transcribe it.
- **Match the `f/kernel` and `f/qemu` prose + command conventions** for
  docstrings, descriptions, and "Equivalent command" lines.
- **Canonical upstream vocabulary.** In prose use the upstream spelling
  (`QEMU`, `NVMe`, `VFIO`, `IOMMU`, `SSH`, `QMP`, `NixOS`) and backtick
  commands/flags/units. Knob names are the upstream tool's own keywords (QEMU
  flag names such as `cpu`, `accel`, `machine_type`), never invented `*_*` names.
  Override Windmill's auto-title-cased field label with a schema `title:` for
  acronyms (`qemu_binary` → `QEMU Binary`, `cpu` → `CPU`, `ram` → `RAM`).
- **Name the consumer, not the generator.** User-facing output (flow/step
  summaries, descriptions, field labels) names the concept it produces,
  such as the `qemu-system@.service` systemd service unit, never the vendored
  generator's codename (`qsu`/qemu-system-units). The codename appears only
  where it is the accurate, structural name: code paths (`f/qsu/`,
  `f.qsu.common`), the kdevops `qsu` ansible role being ported, the vendored
  templates/README, and the `qsu-execution-model.md` design doc. (This mirrors
  the vendored project's own zero-tool-naming rule.)
- **Curated forms, not empty boxes.** A form surfaces the domain knowledge a
  kernel developer or maintainer already carries as named choices, never a raw
  JSON box. Offer the curated set they actually use (the trees, maintainers,
  packages, profiles, protocols) with human labels and featured defaults, so the
  zero-config path is already useful. Keep the full raw config only as a gated
  advanced override that replaces the form when set, never as the primary input.
  Drive every consumer from one curated source. The mirror tree picker
  (`f.workbench.fetch`'s `KERNEL_TREES` checklist plus the advanced `mirrors`
  override, feeding both `fetch` and `mirror`) is the worked example.

The Python here is kept lint-clean and consistently formatted by Ruff, the
single linter and formatter, configured in `pyproject.toml` at line length 88.
`nix run .#format` applies it, and the flake's `lint` check verifies it, run
together with the rest by `nix flake check`. This covers both the repo tooling
under `scripts/` and the hand-authored Windmill step scripts under `f/**/*.py`.
Type-checking uses Pyright (basic mode, with the `f/` rules relaxed for
Windmill's schema-bearing `main()` signatures); it is advisory, surfaced by the
editor and `nix develop .#checks --command pyright`, not by the gate.

Formatting the `.py` under `f/` is safe because `wmill` stores and returns
script bodies verbatim: a canonical `wmill sync pull` reserializes the `.yaml`
metadata and extracts inline scripts, but never rewrites a `.py`. The `.yaml`
and `.lock` sidecars under `f/`, and `wmill-lock.yaml`, stay machine-owned;
never hand-edit them for style. The whitespace and end-of-file checks in
`scripts/check-style.sh` skip all of `f/`; the Python gate is the only style
rule that reaches `f/`, and only for its `.py`.

`f/qsu/bringup.flow/flow.yaml` is generated by `scripts/gen-bringup.py` from the
subflows it composes (`f/qsu/boot`, `f/kernel/build`, `f/nix/build`,
`f/qemu/build`) plus bringup-level transforms in the script itself. To change it:
edit the source subflow schema and/or `gen-bringup.py`, then run
`python3 scripts/gen-bringup.py`. Never hand-edit the generated flow directly.
`python3 scripts/gen-bringup.py --check` (the flake's `generated` check, run by
`nix flake check`) enforces this; it fails if the committed flow drifts from the
generator output.

## Documentation

The prose docs under `docs/` are reStructuredText rendered by Sphinx (locally
through the `docs` devShell, and on Read the Docs). The `toctree` rooted at
`docs/index.rst` is the published tree; the `docs/windmill/*.md` notes are
developer scratch outside it. Build and verify with
`nix develop .#docs --command sphinx-build -b html -W docs <out>` (warnings are
errors); `nix run .#docs` renders and `nix run .#serve` live-renders. Every
`.rst` carries an SPDX identifier on line one, and prose wraps at 80 columns; a
line containing a URL is exempt. Both are enforced by `scripts/check-style.sh`.
The em/en-dash and project-name-versus-command rules in Conventions apply to
docs too.

Hyperlink on first mention. The first time a file's prose names an upstream
project, tool, service, systemd unit or directive, or any man-page-documented
command, link it to its canonical manual or source; later mentions in the same
file stay plain text. Link an internal concept (a flow, a step) to its concept
or reference page with `:doc:`, and link an `f/` flow or step path to its
source with the `:src:` role (below).

A command, tool, or systemd directive reads as code, so link it with the
`:cmd:` role: ``:cmd:`ssh``` renders ``ssh`` (a monospaced literal) hyperlinked
to its manual. The role resolves the name through the one `cmd_links` table in
`docs/conf.py`, the single source for those URLs, so add a name there once and
every page links it the same way; an unregistered name fails the build. An `f/`
flow, step, module, or file path reads as code too, so link it with the `:src:`
role: ``:src:`f/kernel/build``` renders ``f/kernel/build`` hyperlinked to its
source. That role resolves the path against the working tree (the path itself,
then `.flow`, then `.py`), so a flow, a step, a shared module, a subsystem
directory, or a concrete file all link with no per-path table; an unresolvable
path fails the build. For everything else (a project name, any prose-worded
link) use the named-target style, the inline `` `Name`_ `` reference with its
`.. _Name: URL` definition collected at the foot of the file, matching
`docs/concepts/flows.rst`.

Document each flow on its own page under `docs/flows/`. Anything common to
every flow that drives a guest (the job log being the primary view, reaching a
guest over the SSH transport, querying machine and VM status) belongs in the
shared `docs/flows/guests.rst`, linked from the per-flow pages, never copied
into each one.

## Commit rules

All commits must follow these six rules.

1. One commit per change. Atomic commits only; do not mix unrelated changes,
   such as a spelling fix with a code change. When in doubt, leave spelling
   fixes out unless explicitly asked.

2. Write the subject as `subsystem: summary` in the imperative mood, where the
   subsystem names the area changed (for example `fstests`, `qsu`, `nix`,
   `build`, `docs`). Keep the whole subject within 75 characters, following the
   Linux kernel rule that the summary "must be no more than 70-75 characters"
   (Documentation/process/submitting-patches.rst). Aim short; never pad to fill
   the limit.

3. Sign off using the git-configured identity. Check it with
   `git config user.name` and `git config user.email`, then add a
   `Signed-off-by` trailer.

4. Mark AI-generated work with a `Generated-by: Claude AI` trailer placed
   immediately before `Signed-off-by`, with no blank line between them:

   ```
   subsystem: summarise the change in the imperative mood

   Plain-English description of what changed and why, wrapped at 75 columns.

   Generated-by: Claude AI
   Signed-off-by: User Name <user.name@domain.org>
   ```

5. No shopping-cart lists. Write the body as plain-English paragraphs, not
   bullet points or itemised lists, wrapped at 75 columns (trailers are exempt,
   as in the kernel), focused on helping a reviewer understand the
   implementation.

6. Before committing, run `nix flake check` (ruff lint and format,
   generated-file drift, tree formatting) and
   `nix develop .#checks --command bash scripts/check-style.sh` (trailing
   whitespace, missing end-of-file newlines, and commit-message trailer
   formatting). See `docs/contributing/development.rst`.
