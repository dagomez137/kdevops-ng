# Handoff: relocatable build-area paths (WORKTREES_DIR, MIRRORS_DIR): restore/fix reference

Repo: `/home/dagomez/src/kdevops-ng`, branch `explore/wmill-canonical-form`.
Date written: 2026-06-26. Author session focus: making build-area pieces
independently relocatable via env vars. This doc is a **restore/fix reference**:
read it if the relocatable wiring breaks, gets reverted by a rebase, or needs
extending. It deliberately does **not** duplicate the commits, ADR, or memory;
it points at them.

## TL;DR state

Two relocations shipped and committed; one analysis concluded "do nothing"; one
future candidate identified. All work is committed (see hashes below, but match
by **subject** since a concurrent session keeps rebasing and hashes drift).

- **`WORKTREES_DIR`**: worktree-group root, default `WORKBENCH_DIR`. DONE.
- **`MIRRORS_DIR`**: git-mirror root, default `SYSTEM_DIR/mirror`. DONE.
- **bare / ssh**: analysed, **no feature needed** (rationale below).
- **`store/`**: the only remaining size-driven candidate, NOT done, not asked.

Full build-area env contract now:
`WORKBENCH_DIR` ⊃ { `WORKTREES_DIR`, `SYSTEM_DIR` ⊃ `MIRRORS_DIR`, `WORKERS_DIR` }
+ `VENDOR_DIR`.

## The relevant commits (match by subject; hashes drift)

- `worktree: make the worktree-group root relocatable via WORKTREES_DIR`
  (was `518ca94`)
- `docs: document the full tunable env in the nix Configure step` (was `568e621`)
- `workbench: make the git mirror relocatable via MIRRORS_DIR` (was `2243b66`,
  now `c15e234` after rebase)

Inspect with: `git log --oneline | grep -iE 'WORKTREES_DIR|MIRRORS_DIR|tunable env'`
then `git show <hash>`. The commit **bodies** carry the full rationale (the
why was deliberately kept out of inline comments per the repo's
"terse comments" rule: see CLAUDE.md and the `no-explanatory-comments` memory).

## Authoritative sources: do NOT duplicate, read these

- **Design + decisions**: `docs/adr/0008-build-area-layout.md` (Consequences
  section lists every relocatable path incl. `WORKTREES_DIR` and `MIRRORS_DIR`).
- **Glossary**: `docs/concepts/terms.rst` (Workbench, Worktree-group, System
  workbench, Mirror, Bare) and `CONTEXT.md` (the term table + relationships).
- **Operator docs**: `docs/deployment/nix.rst`: "The workbench" section
  (relocatability list + the "reuse work you already have" procedure) and the
  "Configure" section (full per-unit env reference + the `WHITELIST_ENVS`
  gating paragraph).
- **Memory** (`~/.claude/projects/-home-dagomez-src-kdevops-ng/memory/`):
  `build-area-migration-decisions.md` is the running log of every build-area
  decision incl. both relocations, with the exact reasoning and verification.
  Also `windmill-nix-derivation.md`, `wmill-sync-push-only.md`,
  `scoped-git-staging.md`, `cross-host-workbench-b.md`.

## Code surface (where the wiring lives, for fixing)

- `f/common/devshell.py`: the readers. Each is `os.environ.get(VAR)` else a
  default derived from its parent reader: `workbench_dir()` →
  `worktrees_dir()` (default `workbench_dir()`), `system_dir()` (default
  `<workbench>/system`), `mirrors_dir()` (default `system_dir()/"mirror"`),
  `vendor_dir()`. `WORKERS_DIR` is read directly (no reader, mandatory).
  **If a relocation "doesn't take", check the reader's env name + default here
  first.**
- `f/common/worktree.py`: developer worktree path = `worktrees_dir()/<group>/
  <project>`; worker path = `WORKERS_DIR/<idx>/<project>/main`. `system`/
  `workers` are reserved group names (`validate_group`).
- `f/workbench/fetch.py`: bare provisioning. `main()` takes `mirror_dir=""`,
  defaults from `mirrors_dir()`. `_reconcile_alternates()` is **authoritative**
  (rewrites the bare's `objects/info/alternates` to exactly the wanted mirror
  objects dir, dropping stale): this is what makes a mirror move clean. NOTE: a
  concurrent session refactored this file's source tables (`LINUX_SOURCES`,
  `QEMU_SOURCES`, `build_mirrors`); the `mirrors_dir()` wiring survived the
  refactor (still present, now ~line 231).
- `f/workbench/mirror.py`: git-mirror timers. `mdir` defaults from
  `mirrors_dir()`; bakes the path into `git-mirror@.service` ExecStart.
- `f/workbench/fetch.script.yaml` + `mirror.script.yaml`: the Windmill schemas
  expose the `mirror_dir` input. **Hand-maintained** (wmill is push-only; never
  `sync pull`/`generate-metadata`: see `wmill-sync-push-only` memory). If you
  add a step param, add the schema property + `order` entry by hand.
- `deploy/nix/systemd/windmill-worker@.service`: the env contract. Pattern:
  `WORKTREES_DIR` and `MIRRORS_DIR` are **left unset** (so they track their
  parent on a move) but **listed in `WHITELIST_ENVS`** so a drop-in can relocate
  them alone. A step only sees an env if it is in `WHITELIST_ENVS`.

## How to verify / restore the relocatable wiring

1. Readers exist and default correctly:
   `grep -n 'def worktrees_dir\|def mirrors_dir' f/common/devshell.py` (expect 2).
2. Consumers use the readers, not hardcoded paths:
   `grep -n 'worktrees_dir()\|mirrors_dir()' f/common/worktree.py f/workbench/*.py`.
3. Whitelist carries both vars:
   `grep WHITELIST_ENVS deploy/nix/systemd/windmill-worker@.service`: must
   contain `WORKTREES_DIR` and `MIRRORS_DIR`.
4. Gates: `ruff check f/ && ruff format --check f/` (88 cols); `.rst` ≤80 cols
   (`awk 'length>80'`); docs build needs the nix doc shell's pinned Sphinx
   extensions (sphinx_copybutton), so a bare `sphinx-build` fails: that is not
   a content error.
5. Live exercise (only if asked): `wmill sync push`, then run `f/workbench/fetch`
   (or the init flow) with `MIRRORS_DIR` set in a worker drop-in, and confirm
   the bare's `objects/info/alternates` holds exactly the new path.

## bare / ssh: why NO feature (so nobody re-opens it)

The mirror earned its own env for one reason: it is the bulky multi-GB shared
object store you'd park on a separate volume. bare and ssh lack that property:

- **ssh** (`f/workbench/ssh_key.py`, `system_dir()/"ssh"`) is two tiny files
  (ed25519 key + generated config). It already self-heals on a `SYSTEM_DIR`
  move (`_config_header` rewrites absolute paths every run). No size case.
- **bare** (`system_dir()/"bare"/<project>.git`) is small (borrows the mirror's
  objects via the alternate). Relocating it apart from `SYSTEM_DIR` is costly:
  (1) every worktree's `.git` gitdir file holds an absolute pointer into
  `<bare>/worktrees/<name>`, so moving the bare orphans all worktrees; (2) the
  cross-host peer remote URL is derived as `ssh://<peer><SYSTEM_DIR>/bare/
  <project>.git` (`fetch.py` `_ensure_peers`), so a divergent `BARE_DIR` breaks
  the peer contract unless every peer learns it. ADR-0008 keys the bare off
  `SYSTEM_DIR` for exactly this reason.

`SYSTEM_DIR` already moves bare+ssh+store+mirror together (the common case);
`MIRRORS_DIR` covers the one piece that also needed to move apart.

## Next candidate if ever needed: STORE_DIR

`store/` (durable run artifacts / VM images under `SYSTEM_DIR/store`, ADR-0005)
is the only remaining piece that could grow large like the mirror. Same pattern
would apply: add `store_dir()` reader (default `system_dir()/"store"`), point
`f/.../store.py` consumers at it, whitelist `STORE_DIR`, leave it unset in the
unit, document in ADR-0008/terms/CONTEXT/nix.rst. NOT requested; only do it on a
concrete need. Check store keying first: it is identity-based (ADR-0005), so
verify a relocation does not change keys.

## Concurrent-session hazard (READ before touching the tree)

Another session is actively working the same branch. As of this writing:

- It rebased newer commits on top of the relocatable work:
  `workbench: generalize the mirror form to curated per-project config`
  (`f20fd62`) and three worker-worktree "main group" commits (`f6d1427`,
  `7652aba`, `ed51476`).
- The working tree has **uncommitted** changes that are **NOT this work**:
  `f/workbench/fetch.py`, `f/workbench/fetch.script.yaml`,
  `f/workbench/init.flow/flow.yaml`, `f/workbench/mirror.script.yaml`,
  `docs/deployment/nix.rst`.

Therefore: **stage only your own files by explicit path; never `git add -A`**
(see `scoped-git-staging` memory). Do not commit, revert, or `--amend` across
the other session's uncommitted edits. If you must amend the MIRRORS_DIR commit,
confirm nothing has branched off it first.

## Conventions that bit us / must hold

- **Terse comments**: the why goes in the commit body, not inline docstrings.
  python-expert already trimmed three over-explanations from the MIRRORS_DIR
  commit; keep new comments to the non-obvious *what*.
- **Long-form flags**, no em/en dashes in prose, canonical upstream vocabulary
  (QEMU, SSH), `subsystem: summary` ≤75-char subjects, body wrapped ≤75,
  `Generated-by: Claude AI` immediately above
  `Signed-off-by: Daniel Gomez <da.gomez@kernel.org>`. All in CLAUDE.md.
- **wmill push-only**: git is truth; never `sync pull`. Hand-edit the
  `.script.yaml` schema for a real new input; never for style.

## Suggested skills for the next session

- `cli-commands`: any `wmill` use (push, `wmill job` to inspect a failed
  `fetch`/`mirror` run while exercising a relocation).
- `agents-language-specialists:python-expert`: review any change to
  `devshell.py` / `fetch.py` / `worktree.py`, kept within the `f/` conventions.
  It reviewed both relocations clean already.
- `nix`: only if a future change touches Nix-language code. The relocatable
  work did **not** (the env contract lives in the static systemd `.service`
  unit, nothing nix-generated), so `/nix` was N/A here.
