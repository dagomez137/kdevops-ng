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
backends. `podman/` works today; `distro/` and `nix/` are planned. See
`deploy/README.md`. `wmill.yaml` is the workspace-as-code configuration (code
and resources, no secrets, single `kdevops` workspace on branch `main`). `f/`
holds the workspace content and is machine-managed by `wmill`. `Makefile` and
`scripts/` provide the `make style` checks.

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
  does is not obvious; it may state the why as well as the what. The full "why"
  always belongs in the commit body, never as a redundant inline explanation.
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
  such as `QEMU/systemd` or the `qemu-system@.service` unit, never the vendored
  generator's codename (`qsu`/qemu-system-units). The codename appears only
  where it is the accurate, structural name: code paths (`f/qsu/`,
  `f.qsu.common`), the kdevops `qsu` ansible role being ported, the vendored
  templates/README, and the `qsu-execution-model.md` design doc. (This mirrors
  the vendored project's own zero-tool-naming rule.)

Files under `f/` and `wmill-lock.yaml` are generated by `wmill`; never hand-edit
them for style. `make style` skips them.

`f/qsu/bringup.flow/flow.yaml` is generated by `scripts/gen-bringup.py` from the
subflows it composes (`f/qsu/boot`, `f/kernel/build`, `f/nix/build`,
`f/qemu/build`) plus bringup-level transforms in the script itself. To change it:
edit the source subflow schema and/or `gen-bringup.py`, then run
`python3 scripts/gen-bringup.py`. Never hand-edit the generated flow directly.
`python3 scripts/gen-bringup.py --check` (run by `make generated`/`make style`)
enforces this; it fails if the committed flow drifts from the generator output.

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

6. Run `make style` before committing. It checks trailing whitespace, missing
   end-of-file newlines, and the commit-message trailer formatting.
