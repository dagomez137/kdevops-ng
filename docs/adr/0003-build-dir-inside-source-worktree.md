# Build directory is a child of the source worktree

To support cross-host LSP (build on a powerful host, edit/index on another) the
developer artifacts (`compile_commands.json`, `.cmd` files) must relocate across
hosts without path rewriting, which the user explicitly rejected. The kernel's
`Makefile` emits a relative `srctree` — and therefore relative `-I`/`-include`
paths — **only** when the build directory is the source dir or a direct child of
it; a sibling build dir forces absolute source paths. We verified this empirically
(child `O=` ⇒ relative paths, zero absolute-source hits; sibling ⇒ absolute). We
therefore default the build directory to a **child of the source worktree**
(`<canonical>/build`), hidden from `git status` via the worktree's
`.git/info/exclude` (the kernel ignores no generic build dir).

## Status

accepted

## Considered Options

- **Sibling/external build dir** — the conventional layout (pristine source), but
  it forces absolute source paths, so cross-host LSP would require path remapping.
  Kept only as a documented override that **forfeits cross-host LSP**.
- **Path remapping of `compile_commands.json`** — rejected by the user; brittle and
  unnecessary once paths are relative.

## Consequences

- Relative `.cmd` files let a consuming host regenerate `compile_commands.json`
  locally against its own `directory` anchor — no rewrite — which makes same-host
  copy, cross-host `devel`-layer fetch, and local reproduce all relocatable
  uniformly.
- Build artifacts live inside the git checkout; `git clean -fdx` or
  `git worktree remove` would delete the build (needs `--force`) — a documentation
  matter.
- Independent of binary reproducibility: relocatable LSP needs only relative paths,
  not bit-for-bit identical outputs.
- The editor's own clangd resolves a fetched worktree with no Nix devShell and no
  baked `-isystem` paths: it uses its own libclang resource dir for compiler
  builtins and the kernel's in-command `-I`/`-include` for the rest. Confirmed on
  six diverse TUs (`file-not-found=0`, `0` diagnostics) outside the devShell; the
  earlier alpha1 caveat was specific to running `gcc`, which clangd does not.
  Evidence: `~/kernel/repro/alpha1-clangd.{sh,log}`.
