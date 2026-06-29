# Vendor with git-subrepo; carry downstream patches as a rebased stack

ADR-0006 established that the three vendored projects are pinned product source
bumped only by a reviewed pull. This records *how*: each `vendor/<project>` is a
[git-subrepo](https://github.com/ingydotnet/git-subrepo) subrepo, and its
provenance lives in a tool-maintained `vendor/<project>/.gitrepo` file that holds
the upstream `remote`, the `branch`, the pinned upstream `commit`, and the pull
`method`. That file is the machine-readable equivalent of the kernel's
hand-written `lib/zstd` import note. Anyone can read where the tree came from and
at what revision, and `git subrepo pull`/`push` act on it directly, with no
`--prefix` to retype and no pin buried in a squash-commit message.

We chose `git-subrepo` over the two alternatives already in reach.

A `git submodule` keeps the content in a *second* repo, so a plain `git clone` of
kdevops-ng yields empty `vendor/` dirs until `submodule update --init`, and the
`path:` Nix flakes that read `$VENDOR_DIR/nixos-flake` would resolve nothing.
Subrepo content lives inside our tree: clone once and it is all there, buildable,
with no second object store, no detached-HEAD foot-guns, and nothing for
read-only users to install.

A `git subtree` (what we used de-facto) records the pin only in a squash-commit
*message* (`Squashed '...' changes from a..b`) that you must `git log --grep` to
recover, whereas subrepo records it in `.gitrepo`. Subtree also makes you
re-supply `--prefix` on every command, where a wrong prefix causes silent damage;
it loses the thread on a moved subdir; and it has long-standing rebase failures.
Subrepo keys off the subdir, survives renames, and offers `git subrepo status`
plus `git log refs/subrepo/<dir>/fetch` for first-class introspection.

## Carrying downstream patches (the `nixos-flake` case)

`nixos-flake` is not a clean mirror. It is a fork carrying roughly twelve local
patches (the reproducible-build and devShell work) that we intend to coordinate
upstream over time. The canonical git-subrepo model for this differs from
Yocto's, and is worth stating precisely because the lifecycle goal is the same.

In Yocto a recipe keeps pristine upstream plus an external patch stack
(`SRC_URI += "file://0001-foo.patch"`). You carry a `.patch` file until the
change lands upstream, then delete that line. git-subrepo has no separate patch
artifact. A downstream patch is an ordinary git commit sitting on top of the
`.gitrepo`-pinned upstream base, in our mainline history. The lifecycle still
maps across cleanly:

1. Carry. Commit to `vendor/nixos-flake` like any other code. The diff against
   the pinned `commit` is the patch set.
2. Contribute upstream. `git subrepo push vendor/nixos-flake` sends those commits
   to the remote as real, un-squashed history. Alternatively `git format-patch`
   over the same commits produces standalone kernel-style `.patch` emails on
   demand, so we keep Yocto's mailable-patch artifact and generate it only when
   needed.
3. Drop once upstream. With `method = rebase`, a later `git subrepo pull` replays
   our local commits onto the new upstream tip. A patch that has landed upstream
   rebases to an empty step and falls out automatically. That is the same outcome
   as deleting a `SRC_URI` patch line, but driven by git's 3-way merge instead of
   a manual patch-offset refresh, the chore that makes Yocto patch maintenance
   painful. What remains after each pull is exactly the set of patches not yet
   upstream.

So git-subrepo does enable the Yocto "carry until upstream, then drop" workflow.
It expresses the stack as rebased history rather than a quilt series. `method =
rebase` is set for `nixos-flake` for this reason. `qemu-system-units` (a clean
mirror) and `linux-config-fragments` (where we are *ahead* of an outdated
`dagomez137/linux-config-fragments` and will push our work up first) keep the
default `merge`.

## Status

accepted

## Considered Options

- **`git subtree`**: rejected. Provenance only in commit messages, `--prefix`
  bookkeeping, rename-fragile, known rebase failures.
- **`git submodule`**: rejected. Content in a second repo breaks plain-clone and
  the `path:` flakes, for operational overhead with no gain here.
- **A Yocto-style `patches/` directory plus an apply step**: rejected. It
  reintroduces manual patch-offset refreshing and an out-of-band apply stage, for
  source we already track in git where rebase does the same job mechanically.
- **`git subrepo clone --force` to re-establish clean subrepos**: rejected for the
  conversion. It replaces each subdir with upstream HEAD, destroying the
  `nixos-flake` patches and the `linux-config-fragments` lead. We used
  `git subrepo init` instead, which preserves every tree byte-for-byte and leaves
  `.gitrepo`'s `commit` empty until the first deliberate reconciling pull pins it.

## Consequences

- Three `.gitrepo` files now mark the subrepos. Their presence is how a reader and
  the tool know a subdir is vendored. They are inert to the read-only `vendor/`
  container mount, since all subrepo operations run host-side on the dev's
  checkout.
- Collaborators who `pull` or `push` a subrepo need `git-subrepo` installed
  (`source /path/to/git-subrepo/.rc`). Read-only users never do.
- The first `git subrepo pull` per project is a one-time reconcile. With an empty
  pinned `commit`, the tool treats the whole subdir as local change and 3-way
  merges (or rebases) it against upstream. Expect to resolve conflicts once, after
  which `.gitrepo` carries a real pin and subsequent pulls are routine.
- `linux-config-fragments`'s first sync is a `push`, since we lead its upstream,
  then normal pulls once `dagomez137/linux-config-fragments` is refreshed from the
  current work in `~/.git-bare`.
