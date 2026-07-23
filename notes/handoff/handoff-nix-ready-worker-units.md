# Handoff: nix-ready Windmill worker units

Repo: `/home/dagomez/src/kdevops-ng`, branch `main`. This was a **research +
design** session, no code changed. The goal for the next session is to turn
this into a plan (and likely an ADR) for making the `windmill-worker@.service`
units run the worker *already inside* a Nix devShell, so step scripts stop
wrapping every external command in `nix develop … --command`.

## The question

Today every `f/**/*.py` step runs external commands through
`f/common/devshell.py`, which shells out to
`nix develop path:$VENDOR_DIR/nixos-flake#build-kernel --command make …` **per
command**. Each invocation pays a full flake evaluation + shell setup, and every
step hard-codes the wrapper. The user wants workers "already in a nix
environment ready to run any command" (kernel/qemu/systemd/qsu builds) so steps
call bare `make`/`git`/`qemu-img`/`systemctl`.

**Verdict: yes, viable in Windmill OSS.** The design and the source-verified
facts behind it are recorded in memory (do not duplicate; read it first):
`~/.claude/projects/-home-dagomez-src-kdevops-ng/memory/nix-ready-worker-units.md`.

## The three load-bearing facts (verified in source, cite these in the ADR)

Sources read: `~/src/windmill-labs/windmill`, `~/src/nix/nix`,
`~/src/systemd/systemd`.

1. **Windmill (fork v1.738) forwards `PATH` from the worker but `env_clear()`s
   everything else.** Every executor wipes the child env then re-adds a curated
   set (`backend/windmill-worker/src/python_executor.rs:1132-1154`,
   `bash_executor.rs:304-320`). The one var always taken from the worker's *own*
   process env is `PATH` (`PATH_ENV`, `backend/windmill-worker/src/worker.rs:596`,
   injected into every job). **So if the worker starts inside a devShell, its
   PATH carries the toolchain into every job and bare commands resolve.**
   Non-`PATH` vars survive only if named in `WHITELIST_ENVS`
   (`windmill-common/src/worker.rs:2068`, resolved into `WorkerConfig.env_vars`
   ~1974-1981; the OSS equivalent of EE's "env vars passed to jobs"). Init
   scripts cannot seed env (each job is a fresh cleared child). OSS default is
   no nsjail (`DISABLE_NSJAIL` defaults true); leave `ENABLE_UNSHARE_PID` unset.

2. **Nix can pin a devShell into a GC-rooted, eval-free profile and run a
   process inside it.** `nix develop <flake>#shell --profile P` records the
   `-env` closure as a permanent GC root in one step
   (`src/libstore/profiles.cc:93`). Later `nix develop P --command <worker>`
   resolves the profile directly, **skipping flake evaluation**
   (`src/nix/develop.cc:477-495`), and the trailing `exec`
   (`develop.cc:617-623`) makes the worker the in-shell process so children
   inherit the env. The reproducibility `shellHook` (the `-frandom-seed`/`$out`
   rewrites + clang/rust vars) survives because the rc script re-evals it on
   entry (`develop.cc:380`) — **only in the script form, never `--json`**.

3. **systemd's clean launch is an `ExecStart` wrapper under `Type=exec`.**
   `EnvironmentFile=` is `KEY=VALUE`-only and silently drops `export`/functions
   (`man/systemd.exec.xml:3251`), so it cannot consume `nix print-dev-env`
   output. `ExecStart=nix develop P --command windmill` reproduces the full
   devShell; `exec` preserves `MAINPID`/cgroup so `Restart=`/stop work, and
   `Type=exec` surfaces a broken env at start (`man/systemd.service.xml:177-187`).
   Environment-generators and `import-environment` are manager-global (pollute
   every user unit) — reject.

## The proposed design (four parts)

1. **Materialize each devShell as a GC-rooted profile** in the System workbench
   next to `gitbin`/`store-index`/`ccache`:
   `nix develop path:$VENDOR_DIR/nixos-flake#build-kernel --profile $SYSTEM_DIR/devenv/build-kernel`.
   Regenerate only when `flake.nix`/`flake.lock` hash changes. Generalizes the
   existing `_resolve_git` out-link trick in `f/common/devshell.py`.
2. **Worker enters its shell in `ExecStart`**:
   `Type=exec`, `ExecStart=nix develop %S/.../devenv/${DEVSHELL} --command %S/windmill/pkgs/windmill/bin/windmill`,
   `${DEVSHELL}` selected per group via `Environment=`/per-instance drop-in.
3. **Align worker groups/tags 1:1 with shells** (the structural change). Because
   `build-kernel` and `build-qemu` are deliberately incompatible (qemu's
   `NIX_CFLAGS_COMPILE` overflows the kernel host-tool argv: E2BIG on `fixdep`,
   see the comment in `vendor/nixos-flake/flake.nix` and ADR-0004), **one worker
   = one shell.** The single "build" pool splits into a kernel-shell worker and
   a qemu-shell worker; the `vm` group enters the `systemd` shell; transport
   steps use the `transfer` shell. The `bringup.flow` per-step tags must route
   each subflow to the worker whose shell matches.
4. **Whitelist the build-time env vars** so the repro env survives `env_clear()`
   into the job's `make`: `NIX_CFLAGS_COMPILE`, `NIX_LDFLAGS`,
   `CCACHE_CONFIGPATH`, `KERNEL_CLANG_CC`, `KERNEL_CLANG_RESOURCE`,
   `BINDGEN_EXTRA_CLANG_ARGS`, `RUST_LIB_SRC`. `PATH` is free.

## Caveats to resolve in the plan

- **One worker = one shell.** Trades "any worker runs any step" for bare
  commands. Mostly makes the existing per-concern shell split explicit at the
  worker layer. Requires re-tagging flow steps.
- **The whitelist is nix-version-coupled** (cc-wrapper var names drift). Robust
  fallback for the *compile step only*: keep an explicit but now eval-free shell
  entry `nix develop --profile $SYSTEM_DIR/devenv/build-kernel --command make`
  (make is a direct child of `nix develop`, gets the full env, **no whitelist
  needed**), while every non-build op goes fully bare. Decide per step class.
- **Byte-identical cross-host output is the property at stake** (ADR-0004,
  memory `repro-devshell-out-leak`). Verify it is preserved before trusting
  either the whitelist or the profile path.
- **`git` availability per shell**: `build-kernel`/`build-qemu` ship `pkgs.git`,
  the `systemd` shell does not. A `vm` worker in-shell has no bare `git` unless
  the systemd shell gains `pkgs.git` or the `gitbin` out-link stays. Minor.

## Payoff (why it is worth doing)

`f/common/devshell.py` collapses: `DevShell.run("make", …)`, `Git`, and
`Systemd` reduce to one thin "log-the-argv-then-exec-bare" runner (keeps the
CLAUDE.md "compose argv, log once, no shell" rule). The `nix develop #shell
--command` wrapper leaves per-step code entirely; shell *selection* moves up to
the unit. `nix` stays bare on `PATH`. Flake-eval-per-command cost disappears.

## Where the detail already lives (do not duplicate)

- **Memory** `nix-ready-worker-units.md` (the design + citations, authoritative).
  Related memory: `nix-tooling-flake-architecture`, `windmill-nix-derivation`,
  `nix-apps-declarative-deferred` (ADR-0009: this touches deploy/nix, needs the
  deploy session's buy-in), `cross-host-workbench-b`, `worktree-model-worker-vs-developer`.
- **Current code**: `f/common/devshell.py` (the `DevShell`/`Git`/`Nix`/`Systemd`
  runners to collapse), `deploy/nix/systemd/windmill-worker@.service` (the unit
  to change), `vendor/nixos-flake/flake.nix` (the four devShells:
  `build-kernel`, `build-qemu`, `systemd`, `transfer`, and the repro
  `shellHook`).
- **Prior art / constraints**: `notes/adr/0004-reproducibility-normalized-in-devshell.md`
  (repro env), `notes/adr/0008-build-area-layout.md` (`$SYSTEM_DIR` layout for
  the profiles), `notes/adr/0009-nix-run-apps-as-the-task-interface.md`
  (deploy-session ownership of deploy/nix + static units), the deploy handoff
  `notes/handoff/handoff-nix-deploy-session.md`.

## Open / next-session plan

- **Write it up as an ADR** (next free number is **0013**; note the memory line
  says "ADR-0011" but 0011 is now `guest-telemetry-push-over-slirp` and 0012 is
  `staging-workspace-interim`, so that number is stale).
- **Decide the whitelist-vs-eval-free-compile split** (caveat 2) before coding.
- **Design the profile-materialization step**: where it lives (a
  `f/workbench` init step? a nix app?), the flake-hash gate, GC-root placement
  under `$SYSTEM_DIR/devenv/`.
- **Map the flow re-tagging**: which `bringup.flow` steps route to
  kernel-shell vs qemu-shell vs vm workers.
- This is the deploy session's call (touches `deploy/nix` static units + worker
  taxonomy, ADR-0009). Do not implement unprompted; produce the plan/ADR first.

## Suggested skills

- `nix`: read the matching reference (flakes, nixos-modules/systemd,
  home-manager) before touching `flake.nix`/`devShells`/units. The
  home-manager `systemd.user.services` migration (ADR-0009) is an adjacent
  deferred evolution to keep in mind.
- `cli-commands`: `wmill` job inspection when validating a re-tagged worker
  actually pulls the right jobs.
- `verify`: confirm byte-identical cross-host build output after any change to
  how the build env reaches `make` (the repro property is the acceptance gate).

## Repo state to know

- Branch `main`. There are uncommitted `f/fstests/*` and `docs/flows/fstests.rst`
  changes from a **concurrent session** in the working tree; leave them alone.
  Stage only your own files by explicit path (memory `scoped-git-staging`), never
  `git add -A`.
- Work on `main` only: a non-main branch plus a `nix format`/`reflow` run
  auto-adds a junk `wmill.yaml` workspace (memory `qsu-bringup-store-reuse`). A
  doc-only commit does not trigger reflow.
- Commit rules in `CLAUDE.md`: `subsystem:` <=75-char imperative subject;
  `Generated-by: Claude AI` immediately above `Signed-off-by`; run the checks
  before non-doc commits.
