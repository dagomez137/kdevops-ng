# Handoff: nix deployment of kdevops-ng's Windmill stack under systemd --user

Repo: `/home/dagomez/src/kdevops-ng`, branch `explore/wmill-canonical-form`.
This session built and shipped the **nix backend** that deploys kdevops-ng's
core (Windmill server, workers, database, LSP gateway, reverse proxy) from nix
and runs them under `systemd --user`, replacing the podman backend, plus the
docs for deploying and managing it.

## What exists now (done, committed, verified live)

A custom Windmill **1.738.0** server built from the `dagomez137/windmill` fork is
running on `https://localhost:8000`; all six services active; the `kdevops`
workspace (68 scripts / 7 flows) is pushed. The deploy backend lives in
`deploy/nix/`:

- `flake.nix`: packages: `windmill`, `windmill-oracle` (14th lang, unfree
  Oracle), `windmill-frontend`, `postgresql`, `db-setup`, `caddy`,
  `windmill-extra`.
- `windmill/package.nix` (+ `fix-nsjail.awk`, `librusty_v8.nix`, …): the server
  derivation, all 13 free languages; see its header for the build details.
- `windmill-extra/package.nix`: the LSP gateway (full Pipfile parity).
- `bin/windmill-db-setup`: cluster init, password rotation, db creation.
- `systemd/*.service`: six **static** units: `windmill` (server), `windmill-db`
  (postgres), `windmill-extra` (LSP), `windmill-native`, `windmill-caddy`, and
  the single tagged template `windmill-worker@`.
- `Caddyfile`: reverse proxy (internal TLS by default).

There is **no install script**: deploy is `nix build … --out-link`, copy units +
Caddyfile, `systemctl --user enable --now`. Manual flow + management are
documented in `docs/deployment/nix-backend.rst`.

## Where the detail already lives (do not duplicate)

- **Commits** `863ec62`..`1de9cc3` (12 `deploy:` commits) + `e561e8e`,
  `586afb0` (`docs:`) tell the build story step by step (hash resolution, the six
  build fixes, HTTPS, the scriptless static-unit refactor, canonical naming, the
  single tagged worker, the workbench terminology fix). Read the messages rather
  than re-deriving.
- **Memory** (`~/.claude/projects/-home-dagomez-src-kdevops-ng/memory/`):
  `windmill-nix-derivation.md` is the authoritative technical record (the 4 FOD
  hashes, the build gotchas: mold, postPatch-not-line-patches, bindgenHook,
  `auditable=false`, `CARGO_INCREMENTAL=0`, krb5; the deploy model; the
  HTTPS/`IS_SECURE` linkage; the unit-name collision with podman; the canonical
  naming + single tagged worker). Also `wmill-sync-push-only`,
  `scoped-git-staging`, `build-area-migration-decisions`,
  `cross-host-workbench-b`.
- **Docs**: `docs/deployment/nix-backend.rst` (build/deploy/configure/TLS/workers/
  workbench/teardown), `deploy/nix/README.md` (thin pointer), `docs/concepts/terms.rst`
  + `CONTEXT.md` + `docs/adr/0008-build-area-layout.md` (the build-area glossary,
  just reconciled).
- The retired podman backend is reference: `deploy/podman/` (quadlets moved to
  `~/.config/containers/systemd.podman-retired-1000/`, DB data kept).

## State / gotchas to know before continuing

- **Unpushed**: `origin` (the local bare `~/.git-bare/kdevops-ng.git`) is at
  `1de9cc3`; local HEAD is `586afb0`, four commits ahead: two are this session's
  docs, two (`70b9392`, `a4bd1d5`) are a **concurrent session's** docs
  reorganisation. Pushing HEAD pushes all. Push only when the user asks.
- **Concurrent sessions edit this repo.** Stage only your own files by explicit
  path; never `git add -A`. Twice this session another session's uncommitted docs
  had to be stashed/avoided. The docs tree was reorganised by them into
  `docs/{getting-started,concepts,reference,deployment,contributing}/`.
- `systemctl --user` from a detached shell needs
  `XDG_RUNTIME_DIR=/run/user/$(id -u)` and a matching `DBUS_SESSION_BUS_ADDRESS`.
- nix builds need network: run them with the sandbox disabled (the command
  sandbox blocks `cache.nixos.org` and the fetcher cache otherwise).
- `wmill` CLI points at the **direct** server `http://localhost:8002` (bearer
  token), not the HTTPS caddy; the workspace is git-truth, push with
  `wmill sync push` (never `sync pull`).
- Conventions and commit rules are in `CLAUDE.md` (`subsystem:` <=75-char
  subjects; `Generated-by: Claude AI` immediately above
  `Signed-off-by: Daniel Gomez <da.gomez@kernel.org>`; `make style` before
  committing; long-form flags; no em/en dashes; modern unified `nix` CLI).

## Open / candidate next work

- **Push** the two `docs:` commits (and the branch) when the user is ready.
- **`GROUPS_DIR` refactor** (make worktree-groups relocate independently of
  `WORKBENCH_DIR`): a separate `f/` change with its own design tension against
  ADR-0008; a dedicated handoff exists at `/tmp/handoff-groups-dir-refactor.md`.
- **vm/vm-run workers**: the single `windmill-worker@` template can be tagged
  `WORKER_GROUP=vm` per instance, but actually running QEMU jobs needs the
  workbench provisioned (the System workbench: bare mirrors + ssh key, via the
  `f/workbench` init flow) and the `vhost_vsock` module loaded.
- **Oracle (14th language)**: `nix build .#windmill-oracle` is wired but unbuilt
  (unfree Oracle Instant Client, `allowUnfree` scoped in `flake.nix`).
- `deploy/distro/` backend is still a TODO (see `deploy/README.md`).

## Suggested skills

- `nix`: flake/derivation/overlay work for any package changes
  (`deploy/nix/windmill/package.nix`, `windmill-extra`, `flake.nix`).
- `cli-commands`: `wmill` use (push, job inspection) when touching the workspace.
- `preview` / `verify` only if exercising the running UI is needed; otherwise the
  smoke test in `nix-backend.rst` (curl `/`, `/user/login`, `/api/version`,
  `/ws/pyright`) suffices.
