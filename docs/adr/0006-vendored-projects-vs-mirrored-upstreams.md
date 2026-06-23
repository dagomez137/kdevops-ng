# Vendored projects are pinned source, not mirrored upstreams

A worker's disk holds external content of two very different kinds, and the
System workbench should own only one of them. **Mirrored upstreams** (`linux`,
`qemu`) are build *inputs* the product consumes but does not define: they change
thousands of times a day, you always want the latest tip, and correctness never
depends on a specific revision — so they live as a disposable, host-local,
network-refreshed cache under the movable `workers/system/` System workbench
(the Mirror), force-pruned on a timer, never in git. **Vendored projects**
(`nixos-flake`, `linux-config-fragments`, `qemu-system-units`) are the opposite:
they *are* the product's behavior — the devShell that normalizes reproducible
builds, the kernel `.config` fragments that shape what gets built, the systemd
unit templates that boot the VM. A change to any of them changes what kdevops-ng
*does*, so they are carried as pinned source in git under the top-level
`vendor/<project>`, identical on every host that checks out the repo, and bumped
only by a deliberate, reviewed `git subrepo pull` — never by a timer
(ADR-0007).

The decision this records: **the System workbench provisions Mirrors and Bares;
it does not provision vendored projects.** Vendored source is already present on
checkout at `$VENDOR_DIR/<project>` (a sibling of `WORKERS_DIR`, bind-mounted
read-only into each worker); there is nothing to fetch. The only
"init" action a vendored project needs is a re-pin of its consumers
(`nix flake update --flake path:$config_dir nixos-flake`, already done by
`f/nix/lock_config.py`), and the only "update" action is a `git subrepo pull` a
human reviews like any dependency bump. The init-flow TODO that proposed "also
provision the `nixos-flake` devShell and `linux-config-fragments` here" is
therefore mis-framed: it borrowed the Mirror's fetch-into-the-movable-workbench
mechanism for content that must not move with host state.

## Content classes

| Class | Examples | Source of truth | Lifecycle | Home |
| --- | --- | --- | --- | --- |
| **Mirrored upstream** | `linux`, `qemu` | the upstream server | force-refreshed on a timer; disposable | `workers/system/mirror` (movable, host-local, gitignored) |
| **Vendored project** | `nixos-flake`, `linux-config-fragments`, `qemu-system-units` | this repo's git (a pinned copy) | bumped by a reviewed `git subrepo pull`; pinned | top-level `vendor/<project>` (git-tracked, travels with the clone) |
| **Generated state** | `ccache`, build trees, `store-index`, VM runtime | none — reproducible | regenerated on demand | `workers/shared/*`, `workers/<id>/*` (host-local, gitignored) |

## Status

accepted

## Considered Options

- **Provision vendored projects like Mirrors** (the init-flow TODO) — rejected:
  it copies or fetches reviewed product source into the movable, disposable
  System workbench, where activating a new System workbench would tear it down
  and relaunch it. Vendored source is not host state; it must not relocate when
  host state does. Auto-refreshing it on a timer would silently change build and
  boot behavior — the precise outcome a pinned vendor exists to prevent.
- **Reference the upstream projects directly as Nix flake inputs** (no vendor) —
  rejected: it makes every build depend on network reachability to GitHub and on
  upstream not breaking, and it removes the single reviewed commit where a
  behavior change lands. Vendoring keeps the product self-contained and the bump
  auditable. (`nixos-flake` keeps *both* guarantees: vendored in git and
  narHash-pinned in each VM's `flake.lock`.)
- **A movable Workbench that lands away from `vendor/`** — handled by
  *exposure*, not provisioning: a relocated Workbench reaches the vendored
  projects through a symlink to the repo's `vendor/<project>`, so there is still
  exactly one pinned copy. No fetch, no second source of truth.

## Consequences

- The init flow's vendored-projects TODO is closed by *deletion of the task*,
  not implementation: `f/workbench/init` owns Mirror + Bare + SSH; it gains no
  vendored-fetch step. The misleading TODO comment is removed.
- `git subrepo pull` becomes the documented update path for each vendored
  project (ADR-0007), paired with `nix flake update nixos-flake` to re-pin the
  consumer lock. This is a developer action, never a timer.
- The vendored source moved out of the runtime tree to the top-level `vendor/`,
  so the class boundary is now self-evident: `vendor/` is tracked product source,
  everything under `workers/` is host-local runtime. Workers reach `vendor/`
  through `VENDOR_DIR` — a sibling of `WORKERS_DIR`, bind-mounted **read-only** at
  the same absolute path (asserting the pinned-not-mutated invariant), with a
  `WORKERS_DIR`-sibling fallback for local `wmill script preview` runs.
- Each vendored project carries its provenance record as a `.gitrepo` file
  (upstream remote, branch, pinned commit, pull method) maintained by
  `git-subrepo` — the same role the kernel's hand-written `lib/zstd` import note
  plays, but machine-readable and tool-maintained. ADR-0007 records why
  `git-subrepo` over `git subtree`/`git submodule`, and how the `nixos-flake`
  fork carries downstream patches.
- The movability boundary itself becomes the sorting rule: content that *should*
  tear down and relaunch with a relocated System workbench is host state
  (Mirror, Bare, SSH); content that should travel with the repo is vendored
  source; content that should never travel at all is generated state.
