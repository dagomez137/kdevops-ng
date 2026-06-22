# A custom content-addressed store for build outputs, not the Nix store

The kernel and QEMU are built with `make`/`ninja` inside a pinned Nix devShell — not
as Nix derivations — because development needs fast incremental builds over a mutable
worktree (b4 series, branch hacking), which hermetic from-scratch derivations
preclude. Their outputs therefore live in a worktree destdir, outside `/nix/store`,
so Nix's native reuse (already-realised paths) and cross-host fetch (substituters /
`nix copy`) do not apply to them. We content-address those outputs ourselves: a build
identity (a hash of the config, the devShell `drvPath`, the make flags and the source
commit) keys a versioned destdir, `reuse_check` skips a build whose identity is
already present, and `fetch_identity` rsyncs it from a peer.

## Status

accepted

## Considered Options

- **Build as Nix derivations.** Would give reuse, `nix copy`/substituter transport
  and reproducibility for free, but rebuilds the whole derivation on any input change.
  Rejected: it destroys the incremental edit-build-test loop kernel/QEMU development
  depends on. (nixpkgs builds *release* kernels this way; that is the wrong model for
  an iterate-and-test loop.)
- **Custom identity + Nix store transport.** Keep the make build and our input-hash
  identity, but `nix store add` the artifacts and ship them with `nix copy` / a binary
  cache instead of rsync. Not taken now; recorded as the likely future transport.

## Consequences

- We use Nix where it fits and not where it does not: the toolchain is a pinned
  devShell (and its `drvPath` is our toolchain identity — validated byte-identical
  across two hosts from one `flake.lock`), while the mutable build and its outputs are
  ours.
- The input-hash identity is unavoidable regardless of transport: it must be known
  *before* the build to skip it, and Nix gives a pre-build key only for derivations
  (content-addressed derivations are computed *after* the build, so they offer
  downstream early-cutoff, not skip-the-build-itself).
- We under-use Nix on the store/transport layer: `fetch_identity`'s rsync reinvents
  `nix copy` (signatures, path-level dedup, GC, and the build-farm-as-binary-cache
  model), and the guest already mounts `/nix/store`. A prototype against a second host
  (`~/kernel/repro/nix-copy-proto.{sh,log}`) confirms the loop: host B `nix store
  add-path`s its install tree, host A `nix copy --from ssh://B` fetches it (411 MiB in
  ~4 s), the transported `qemu-system-x86_64` runs with zero missing RPATH dependencies
  because the two hosts share one toolchain closure (the beta2 result), and a second
  `nix copy` is a no-op — store-path validity is `reuse_check`, for free. The prototype
  also bounds the claim: a plain `add-path` registers *no* references, so closure-level
  dedup against the toolchain is not automatic — it needs the artifact to be a
  derivation output (scanned references), not just an added path. Migrating
  `fetch_identity` to this is a contained change — the identity and `reuse_check` logic
  are transport-independent — and is the expected evolution if the rsync path proves
  limiting.
