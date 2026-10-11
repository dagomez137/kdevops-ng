# kdevops-ng

A from-scratch project that self-hosts a Windmill instance and manages its
workspace content (scripts, flows, apps, resources, triggers) as code in git.
The instance runs locally and is reachable only on `127.0.0.1:8000`
(SSH-forward to use the UI). `wmill sync pull` / `wmill sync push` move
workspace content between the instance and this repository; git is the source
of truth. The repository name predates a fuller description that will be added
later.

## Privilege boundary: user mode only (never bypass)

Everything runs as the unprivileged user: no `sudo`, no write outside the
user's own paths, no system manager or system bus (`systemctl` without
`--user`, `busctl --system`). Whatever needs root is a one-time manual step
listed with its commands in `docs/getting-started/requirements.rst`; code
never runs, wraps, renders or prints those commands. A step may check a
root-owned precondition with user-readable reads and fail with a pointer to
that page; an agent asked to automate a root step stops and asks.
`scripts/check-style.sh` (3b) refuses an escalation in code. Why, and the
design it comes from: `docs/concepts/principles.rst`.

## Nix first: everything reproducible (never bypass)

Every tool, library, service and guest component comes from Nix, pinned: the
root `flake.nix` for the repository tooling, `vendor/nixos-flake` for the
devShells steps run in and what guests boot, `deploy/nix` for the Windmill
stack. Never a distro package, a container runtime, a language package manager
outside a Nix derivation, a piped installer, or a binary that happens to be on
the host's `PATH`; steps run every command through the `f/common/devshell`
runners, and a tool nixpkgs lacks is packaged in `vendor/nixos-flake`, never
fetched at run time. `scripts/check-style.sh` (3c) refuses the rest in code;
a derivation running `npm` in its own build (`deploy/nix/wmill/package.nix`)
is the one sanctioned shape. Why: `docs/concepts/principles.rst`.

## systemd first: let the service manager do it (never bypass)

Anything that runs for a while, needs supervising or must stop cleanly is a
systemd unit, and the project uses what systemd already does: timers instead
of cron, the journal as the log and results channel, transient units or scopes
(`systemd-run`, or `StartTransientUnit` over the bus) for work that must be
watched or killed, `systemctl` run on a guest over the SSH transport to reach
its manager, settings through systemd's own mechanisms (`EnvironmentFile=`,
specifiers, drop-ins, `StandardInputText=`) rather than a shell string, and an
upstream template (`modprobe@`, `kmod-static-nodes`) before a new one. No
daemons, wrapper scripts, PID files, `nohup`/`setsid`/backgrounded processes,
or polling loops standing in for `Requires=`/`After=`. Host units are user
units. `scripts/check-style.sh` (3d) refuses detached processes and cron in
code. Why: `docs/concepts/principles.rst`.

## Everything runs in a sandbox (never bypass)

A step works only inside what it owns: a worker in its Worker sandbox
(`WORKERS_DIR/<index>/main/...`), the one place it may reset, clean or force;
shared state in the System workbench (`SYSTEM_DIR`); the vendored projects
beside the Workbench (`VENDOR_DIR`); every path from the env contract
(`WORKBENCH_DIR`, `WORKERS_DIR`, `SYSTEM_DIR`, `VENDOR_DIR` and the derived
`WORKTREES_DIR`, `MIRRORS_DIR`, `CCACHE_DIR`, `STORE_INDEX_DIR`), never
hard-coded. A Developer's worktrees are never built in, reset or have a
branch moved under them, and are removed only when git itself agrees nothing
is lost; work reaches a worker only as a ref in the Bare. By default a guest
exchanges data only through its per-VM share, whose path a step checks stays
under its root. Outside the Workbench a step writes only user units under
`~/.config/systemd`, VM state under `~/.local/state/qemu-system` and scratch
under `$TMPDIR` (unset in a job, so `/tmp`), never a user file
(`~/.gitconfig`, `~/.ssh/config`): a line
a host tool needs there is a manual step, listed in `requirements.rst` ("Your
own configuration"). Nothing enforces this at runtime, so every change keeps
it by construction. Why:
`docs/concepts/principles.rst`; sources: `CONTEXT.md`, `notes/adr/0008`,
`notes/adr/0010`, `f/common/worktree.py`.

## Commands a developer can run (never bypass)

Whatever a step does, a developer must be able to do by hand from the job log:
every external command printed exactly as it runs, with the environment and
directory it needs, and every written file named. No Ansible-style module
that hides work, no hidden environment; a shell string handed to `sh -c` is
a last resort, printed whole (the seven that remain are listed in
`notes/plans/command-plans.md`). The Conventions below ("Compose commands
explicitly", "Don't restate what the runner logs", the "Equivalent command"
docstrings) are how this is kept today; the generation/execution split
planned in `notes/plans/command-plans.md` is the way forward for runner work.
Why: `docs/concepts/principles.rst`.

The five rules have one home for developers, `docs/concepts/principles.rst`;
keep it
and these sections in step.

## Layout

`docs/getting-started/quickstart.rst` is the minimal end-to-end setup.
`deploy/nix/` deploys the instance (see `deploy/README.md`). `wmill.yaml` is the
workspace-as-code configuration (code and resources, no secrets; one
workspace, `kdevops`, on branch `main`). `f/`
holds the workspace content and is machine-managed by `wmill`. Deploy through
the `deploy-kdevops` nix app, which pushes the whole tree and then runs the
preview smoke suite against the deployed steps. The root `flake.nix`
and `scripts/` provide the checks, run with `nix flake check` (see
`docs/contributing/development.rst`).

## Vendored projects

`vendor/nixos-flake`, `vendor/qemu-system-units` and
`vendor/linux-config-fragments` are git-subrepo copies of their upstream
repositories, not submodules: `vendor/<name>/.gitrepo` names the upstream, the
branch and the pinned commit, `git subrepo pull` bumps one and `git subrepo
push` sends local commits upstream, with the tool in the root flake's default
devShell (`nix develop --command git subrepo status`). A change inside one is an
ordinary commit here, a downstream patch until it lands upstream, written to
that project's own `CLAUDE.md`, commit style included; `scripts/check-style.sh`
skips `vendor/`. How and why: `vendor/README.md`, `notes/adr/0006`,
`notes/adr/0007`.

## Conventions

Always use long-form command flags everywhere (scripts, docs, examples): for
example `mkdir --parents`, `rm --recursive --force`,
`nix build --out-link`, `git log --format`, `systemctl --user show --property`,
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
together with the rest by `nix flake check`. This covers the repo tooling
under `scripts/`, the hand-authored Windmill step scripts under `f/**/*.py`,
and the fixture tests under `tests/`, which exercise the pure logic of the
`f/` step modules (parsers, verdict rules, store reads) in the flake's
`tests` check with no instance and no network.
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
command, link it to its canonical manual or source; the first time a flow page
names one of its own steps (`discover`, `render_config`, `wait`), link that step
to its source too. Later mentions in the same file stay plain text. Link an
internal concept (a flow, a step) to its concept or reference page with `:doc:`,
and link an `f/` flow or step path to its source with the `:src:` role (below).

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
path fails the build. When prose names a step or flow by its bare name, link the
first mention with the explicit-title form so the short label stays readable:
``:src:`discover <f/fstests/discover>``` renders ``discover`` (a monospaced
literal) hyperlinked to its source, and later mentions stay plain. For everything
else (a project name, any prose-worded link) use the named-target style, the
inline `` `Name`_ `` reference with its
`.. _Name: URL` definition collected at the foot of the file, matching
`docs/concepts/flows.rst`.

Document each flow on its own page under `docs/flows/`. Anything common to
every flow that drives a guest (the job log being the primary view, reaching a
guest over the SSH transport, querying machine and VM status) belongs in the
shared `docs/flows/guests.rst`, linked from the per-flow pages, never copied
into each one.

Code blocks say where they run and where their output comes from. A
`.. code-block::` of commands run on the guest (a `#` root prompt over the SSH
transport), or of output the guest produced (a unit listing, a KTAP or failure
diff, a SysRq stack), carries `:class: cmd-guest`; one run on or produced by
the operator host (a `$` prompt, including the `systemctl --host <vm>` and
`ssh <vm> ...` forms that target a guest) carries `:class: cmd-host`. The class
draws a colored left accent (guest green, host blue) defined once in
`docs/_static/custom.css`. Caption only the first block of each kind on a page
(`:caption: guest` / `:caption: host`), whether that first block is a command or
its output; later blocks of that kind take the accent alone, so the label is
introduced once and the color carries the rest. A doc diagram, a config
fragment, or a file-content sample is not host or guest content and stays
unlabelled. The prompt is not decisive: classify by where the content
originates, so an on-guest `echo ... > /sys/...` is `cmd-guest` with a `#`
prompt even where a source shows it with `$`, and a guest's output is
`cmd-guest` in a plain `text` block with no prompt at all.

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

4. Mark AI-assisted work with an `Assisted-by: LLM` trailer ahead of the
   `Signed-off-by` that closes the message, as the kernel places it:

   ```
   subsystem: summarise the change in the imperative mood

   Plain-English description of what changed and why, wrapped at 75 columns.

   Assisted-by: LLM
   Signed-off-by: User Name <user.name@domain.org>
   ```

   Credit other contributors as the kernel's
   Documentation/process/submitting-patches.rst does ("When to use Acked-by:,
   Cc:, and Co-developed-by:"). The commit author is the primary author. A
   co-author gets a `Co-developed-by` immediately followed by their own
   `Signed-off-by`, which only they can give; the sign-offs run in the order
   the change passed through hands, so the last is always the committer's.
   Someone who wrote none of it but approves it gets an `Acked-by` or
   `Reviewed-by`, again only with their permission:

   ```
   Co-developed-by: Co Author <co.author@domain.org>
   Signed-off-by: Co Author <co.author@domain.org>
   Assisted-by: LLM
   Signed-off-by: User Name <user.name@domain.org>
   ```

   This is the kernel's own tag, adopted here for the same reason rule 2
   takes the kernel's subject limit: a contributor moving between this
   repository and the kernel should not have to keep two conventions in
   mind. The tag arrived in v7.0-rc1 with commit 78d979db6cef ("docs: add
   AI Coding Assistants documentation"), which added
   Documentation/process/coding-assistants.rst and defines the format as
   `Assisted-by: LLM [TOOL1] [TOOL2]`; 6252e5c1c20e ("docs: add an
   Assisted-by mention to submitting-patches.rst") then made the
   acknowledgment a submission requirement. The optional tool names are for
   specialized analysis tools actually run (coccinelle, sparse, smatch,
   clang-tidy); basic tools such as git, gcc, make and editors are never
   listed, so the bare `Assisted-by: LLM` is the usual form. Earlier
   commits here and in the vendored subprojects carry
   `Generated-by: Claude AI`, which this replaces; leave those alone.

5. No shopping-cart lists. Write the body as plain-English paragraphs, not
   bullet points or itemised lists, wrapped at 75 columns (trailers are exempt,
   as in the kernel), focused on helping a reviewer understand the
   implementation.

6. Before committing, run `nix flake check` (ruff lint and format,
   generated-file drift, fixture tests, tree formatting) and
   `nix develop .#checks --command bash scripts/check-style.sh` (trailing
   whitespace, missing end-of-file newlines, and commit-message trailer
   formatting). See `docs/contributing/development.rst`. The trailer check
   reads HEAD; linked as the repository's commit-msg hook, `scripts/commit-msg`
   runs it on the message before the commit lands.
