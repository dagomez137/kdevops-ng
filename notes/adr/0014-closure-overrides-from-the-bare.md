# Closure package overrides build from the Bare

The NixOS closure lets five packages be rebuilt from source instead of their
pinned versions: fio, xfstests, xfsprogs, libbpf-tools and blktests, each a
tree a kernel developer patches while testing. Today the form gives each
package a free-text `src` (an absolute path or a git/flake URL) and `ref`,
and `f/nix/render_config` renders them as a `<pkg>-src` flake input verbatim.
The typical `src` is an absolute path at a developer checkout, which makes
the worker's `nix build` read a developer's worktree directly. That violates
the worktree model twice: ADR-0010 separates the worker build site from the
developer worktree, and the glossary's Developer entry states that a
developer hands work to a worker only by publishing a ref to the Bare. The
free-text box also fails the curated-forms rule, while `f/kernel/build` and
`f/qemu/build` already carry the worked alternative: a ref picker off the
project's Bare, a Custom Ref escape, b4 series application, and a developer
worktree deploy tail.

Give the five closure packages the same treatment. A package-to-project map
beside `_OVERRIDABLE_PKGS` names the mirror project whose Bare carries each
package's source:

```
fio          -> fio
xfstests     -> xfstests-dev
xfsprogs     -> xfsprogs-dev
libbpf-tools -> bcc
blktests     -> blktests
```

The form replaces each package's free-text `src`/`ref` with the kernel-style
triple: a `ref` dynselect off the project's Bare through
`f.common.gitrefs.list_refs`, a `custom_ref` toggle, and a free-text
`git_ref` shown when the toggle is on. A blank pick keeps the pinned
version. `f.common.gitrefs` gains two abilities the package trees need.
First, `list_refs` also lists the Bare's `mirror/<branch>` remote-tracking
refs: a package Bare has no local heads until a developer pushes one, so the
upstream branch tips (`mirror/master`, `mirror/for-next`) that are the
primary picks for a test tree were invisible to the picker. Second, a
`qualify_ref` helper resolves a picked or typed value to a fully qualified
refname in the worktree model's resolution order (tag, then the mirror
remote's branches, then a developer branch, then any remote-tracking ref),
because Nix treats an unqualified ref as `refs/heads/<ref>` and the
upstream branches live under `refs/remotes/mirror/*`.

`_override_input` renders a curated override as a git-type input cloning
from the host-local Bare at the qualified ref:

```
fio-src = {
  type = "git";
  url = "file:///<SYSTEM_DIR>/bare/fio.git";
  ref = "refs/remotes/mirror/master";
  submodules = true;
  flake = false;
};
```

A full 40-hex custom ref renders a `rev` pin instead of a `ref`. Nix clones
from the Bare (objects borrowed from the mirror through alternates),
`flake.lock` pins the commit, and the existing `lock_config` re-lock keeps
tracking the branch tip on every build, so a freshly pushed commit lands in
the next closure. A tag pick makes the re-lock a no-op and a `rev` pin is
explicit, both correct. All three fetch forms were live-verified against
the deployed Bares on 2026-08-13 (`refs/remotes/mirror/*`, `refs/tags/*`,
and a mid-history rev-only pin, each locking to a concrete commit).
`extra_overrides` stays as the gated raw escape and keeps the old free-form
rendering, including the absolute-path form, for any other nixpkgs package.

The vendored nixos-flake needs no change: the input declaration is wholly
owned by `f/nix/render_config`, and all five vendored recipes are already
source-form agnostic. bcc keeps `submodules = true`; its `.gitmodules` names
absolute GitHub URLs (libbpf, bpftool, blazesym), so submodules fetch from
upstream over the network at input-fetch time exactly as they do today. The
mirrors do not carry submodule objects and this ADR accepts that.

Later phases extend the parallel with the kernel and QEMU builds: a
developer-tail step lays or refreshes `WORKTREES_DIR/<group>/<project>`
onto the built ref through `prepare_developer` (never destructive), so one
topic group gathers linux, qemu and xfstests-dev on both sides, and b4
series application reuses `f.common.worktree.prepare`, which applies the
series in the worker worktree and publishes `b4/<slug>` to the Bare; the
flake input then refs that published branch. The Bare stays the one channel.

## Status

accepted

## Considered Options

- **Free-text `src`/`ref` inputs (the prior form).** Rejected: the common
  path points the worker at a developer checkout, bypassing the Bare ref
  channel and the worker/developer split; the form is an empty box rather
  than the curated set of refs a developer actually builds.
- **Resolve every pick to a `rev` sha at render time.** Rejected: the
  branch-tip re-lock on every build is what makes bringup pick up freshly
  committed patches without a manual bump; a hard pin would freeze the
  first tip. A full-sha custom ref still gives an explicit pin on demand.
- **Keep remote upstream URLs for the git inputs.** Rejected: every build
  would fetch over the network and the picked ref would name upstream
  state, not the host's Bare, so a developer branch could not be built at
  all. The Bare is the one ref channel and it is local.
- **Mirror bcc's submodules so the whole fetch is local.** Rejected: Nix
  resolves submodule URLs from `.gitmodules`, which names absolute GitHub
  URLs; redirecting them needs git `insteadOf` machinery in the worker's
  config for no real gain over the status quo, which already fetches
  submodules from upstream on every git-form override.
- **One shared worktree group knob across the five packages now.**
  Rejected for phase 1: the packages override independently at independent
  refs, and a group name only means something once the developer tail
  exists; the phase-2 tail adds the `worktree_group` knob aligned with the
  kernel and QEMU forms.
- **Bare-backed git inputs at a picked, fully qualified ref (chosen).**
  The worker clones from `file://$SYSTEM_DIR/bare/<project>.git`, the lock
  pins the commit, the re-lock tracks the tip, and every ref that reaches
  a build passed through the Bare.

## Consequences

- The worker never reads a developer checkout: the curated form cannot
  express one, and the only remaining path escape is the gated
  `extra_overrides`. The glossary's ref-channel doctrine holds without
  amendment.
- `list_refs` lists `mirror/<branch>` rows for every repo, so the kernel
  and QEMU pickers gain the mirror branch tips too, ordered after the
  developer branches and before the tags.
- A ref that resolves nowhere in the Bare fails the render step with the
  attempted namespaces named, rather than failing later inside Nix; a
  missing Bare fails with a pointer to `f/workbench/init`.
- `lock_config` needs no change: its `<pkg>-src` regex already matches the
  new inputs and re-locks them to the tip each build.
- `scripts/gen-bringup.py` must carry the five new dynselect helpers in its
  `DYN_SELECT_CODE`, since an embedded picker's helper does not travel with
  the schema; the flake's `generated` check enforces the regeneration.
- The closure's identity is unchanged: `flake.lock` pins the commit, the
  render step's job log prints the full rendered flake, and the sidecar
  reuse model of ADR-0013-era bringup is untouched.
- Saved run inputs carrying the old per-package `src` stop applying; the
  same source now needs its ref published to the Bare, or the
  `extra_overrides` escape.
