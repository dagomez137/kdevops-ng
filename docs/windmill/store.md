# The build Store

The Store lets an identical kernel or QEMU be **reused or fetched instead of
rebuilt**, on one host or across hosts. A build is keyed by a reproducible
*build identity*; that identity is published to the Nix store and indexed, so a
later build with the same identity skips compilation and a peer's build can be
pulled over the network. The decision rule each build follows is: **reuse a local
build → fetch a peer's → otherwise build**.

Why the Nix store and not a bespoke artifact server is recorded in
[ADR 0005](../adr/0005-custom-store-not-nix-store.md). The short version: the
toolchain is already a pinned Nix devShell, so two hosts building from one
`flake.lock` get a byte-identical toolchain closure; publishing the build outputs
to the same store and moving them with `nix copy` reuses that machinery instead
of reinventing `rsync`.

## Build identity

The identity is a short hash over the inputs that fix a build's bytes: the
`.config` (minus its localversion), the `build-kernel`/`build-qemu` devShell
derivation path (the toolchain), the make flags (host paths normalised), and the
source commit. See [ADR 0002](../adr/0002-build-identity-in-kernelrelease.md).

- **Kernel** bakes the identity into `CONFIG_LOCALVERSION`, so `uname -r` self-reports
  it: `7.1.0-rc7-<hash>`. Same identity ⇒ same bytes ⇒ one release name.
- **QEMU** has no release string, so the identity keys the install prefix
  `destdir/<identity>`.

## Two layers per identity

A build publishes up to two independent store paths:

| Layer | Name | Contents | Consumer |
|---|---|---|---|
| **run** | `kernel-<release>` / `qemu-<identity>` | boot image + `lib/modules/<release>` / the QEMU install tree | booting a VM (`f/qsu`) |
| **devel** | `kernel-devel-<release>` | the build dir's `.cmd` command database + generated headers/sources | clangd / LSP index on a worktree |

They are separate so a boot fetch stays lean (it never drags the ~190 MB devel
layer) and a developer fetching an index never pulls boot images. The devel
layer's composition and the allowlist that builds it are documented in
`f/kernel/publish_devel.py`.

## The catalog

Every published identity is a symlink under `WORKERS_DIR/shared/store-index/`:

```
kernel-7.1.0-rc7-b9e826508b1e        -> /nix/store/<hash>-kernel-7.1.0-rc7-b9e826508b1e
kernel-devel-7.1.0-rc7-b9e826508b1e  -> /nix/store/<hash>-kernel-devel-7.1.0-rc7-b9e826508b1e
qemu-<identity>                      -> /nix/store/<hash>-qemu-<identity>
```

Each symlink is also a Nix **GC root** (created with `nix build --out-link`), so
the store path survives `nix store gc` until the entry is
removed. The catalog is the authoritative, host-local list; store-path *names*
alone are noisy (nixpkgs ships its own `-kernel-*` paths). A peer's catalog is the
same directory read over ssh.

## How the build flow uses it

The kernel and QEMU build flows wire these steps (skipped on reuse, so they only
run after a real build except where noted):

- **`reuse_check`** runs before the compile and reports whether the identity is
  already available: checking the local destdir/prefix first, then the store
  catalog (where a fetched build lives). When present, configure/compile/install
  are skipped and the manifest points at the existing artifacts. It is
  store-aware, so a fetched identity is consumed *in place* from `/nix/store` with
  no local copy.
- **`fetch_identity`** runs before `reuse_check`; with a peer configured it reads
  the peer's catalog entry over ssh, pulls the store path with `nix copy`, and
  indexes it locally, leaving the run layer in the store for `reuse_check` to
  resolve.
- **`publish`** / **`publish_devel`** run after a real install and add the run /
  devel layer to the store and the catalog.
- **`fetch_devel`** is a standalone developer step: it resolves
  `kernel-devel-<release>` (local or from a peer), copies the developer subset into
  the worktree's build dir, and regenerates `compile_commands.json` locally so the
  index points at that worktree's own source.

## Cross-host fetch (the `prebuilt` knobs)

The kernel and QEMU build flows expose a **Prebuilt** input group:

- `remote`: the ssh host of a peer builder.
- `remote_index`: that peer's `store-index` directory (its
  `WORKERS_DIR/shared/store-index`).

With both set, `fetch_identity` learns the peer's store path from
`ssh <remote> readlink <remote_index>/<name>` and pulls it with `nix copy --from
ssh://<remote>`. Because the two hosts share one toolchain closure, a transported
QEMU binary runs with zero missing dependencies. All cross-host I/O happens inside
the `transfer` devShell (`nix` + OpenSSH); nothing uses `rsync`.

This moves build *outputs* across hosts. Build *inputs* (a developer's branch) cross
the other way, by git: see [cross-host dev branches](cross-host-dev-branches.md).

> ssh prerequisite: the `transfer` devShell's OpenSSH rejects a group-writable
> `~/.ssh/config` ("Bad owner or permissions"); keep it `0600`.

## Inspecting and pruning: `f/common/store_index`

`store_index` reads and maintains the catalog:

- `list` (default): the local catalog with sizes and validity, plus a peer's when
  `remote`/`remote_index` are set.
- `inspect <name>`: one identity's store path, closure size and validity.
- `forget <name>` (with `confirm`): drop one entry's GC root so
  `nix store gc` can reclaim its store path. The build leaves the catalog
  but is rebuildable.
- `prune`: drop every entry whose store path was already collected (dangling).

By hand the same is:

```sh
ls -l "$WORKERS_DIR"/shared/store-index/                                   # list
nix path-info --closure-size --human-readable "$(readlink .../<name>)"     # inspect
rm "$WORKERS_DIR"/shared/store-index/<name> && nix store gc                # forget + reclaim
ssh <host> ls "$WORKERS_DIR"/shared/store-index/                           # a peer's catalog
```
