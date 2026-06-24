# Cross-host dev branches

A developer on one host can hand a branch to a worker on another host to build:
"develop on the thin box, build on the beefy one." Build *outputs* already cross hosts
through [the build Store](store.md); this is the *input* (ref) side, the per-host ref
channel of [ADR-0001](../adr/0001-bare-is-the-working-repo.md) and the cross-host
relationships in [`CONTEXT.md`](../../CONTEXT.md).

Same-host, a developer and a worker share one **Bare**, so publishing a branch is just
a commit and the worker builds it. Cross-host, the developer **pushes** the branch to
the peer's Bare over ssh, and the peer's worker builds it from its own
`refs/heads/*`: no build-flow change, because `prepare()` already resolves a literal
ref against the Bare.

## Provisioning the peer remotes

Run `f/workbench/init` (over `f/workbench/fetch`) with a `peers` list of the **ssh-host
aliases** of the other hosts:

```sh
wmill flow run f/workbench/init --data '{"peers": ["hetzie"]}'
```

Each alias becomes a `<peer>` remote on every Bare, its URL the peer's Bare under the
**same `SYSTEM_DIR` layout**:

```
ssh://<peer>/<SYSTEM_DIR>/bare/<project>.git
```

with a `+refs/heads/*:refs/remotes/<peer>/*` refspec. The derivation assumes peers
share this host's `SYSTEM_DIR` path (true when the hosts share a home, e.g. one
NFS/`/home`); provisioning only wires the remote, it does not fetch, since push is the
workflow and a peer may be empty or unreachable. List peer hosts, not self.

> ssh prerequisite: the same passwordless ssh the Store uses (the `transfer` devShell's
> OpenSSH; keep `~/.ssh/config` `0600`). A peer alias resolves through that config.

## The workflow

1. **Both hosts provision peers** (symmetry): on host A `peers: ["B"]`, on host B
   `peers: ["A"]`. Each Bare now has a remote for the other.
2. **Publish a branch** from a developer worktree on A (the worktree shares A's Bare,
   so it inherits the `<peer>` remote):

   ```sh
   git -C <worktree> push B HEAD:refs/heads/<branch>
   ```

   The branch lands in B's Bare `refs/heads/<branch>`.
3. **Build it on B**: run B's build flow with the branch as the ref:

   ```sh
   wmill flow run f/kernel/build --data '{"worktree": {"git_ref": "<branch>"}}'
   ```

   B's `prepare()` resolves `<branch>` locally (it is now a `refs/heads/*` entry in B's
   Bare, no fetch needed), lays its warm `main` worktree at `B`'s
   `<NNNN>/linux/main`, and builds. The build's run layer can then come **back**
   to A through the Store (`prebuilt` `remote`/`remote_index`), closing the
   "build on B, boot on A" loop.

## Direction and reuse

The `refs/remotes/<peer>/*` refspec also lets a host **fetch** a peer's dev branches
(`git -C <bare> fetch <peer>`), the symmetric read direction; the push above is the
default developer flow. The peer remotes are independent of the `mirror`/upstream
remotes and the Store catalog: refs (build inputs) cross by git, Store entries (build
outputs) cross by `nix copy`.
