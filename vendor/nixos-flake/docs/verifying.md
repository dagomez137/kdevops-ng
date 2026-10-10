# Verifying changes

Run these steps before every commit. They are manual for now; whether
and how to wire them as git hooks is a later decision. Each step states
why it exists and what it does.

## 1. Format — `nix fmt`

Why: the whole history is `nixfmt-rfc-style`-clean; a commit that drifts
breaks that invariant.

What: rewrites every tracked `.nix` file in place to the canonical
style. Run `git diff` afterwards — a non-empty diff means your change
introduced unformatted code; stage the reformat.

The formatter is `nixfmt` itself, which takes files. Give it the tracked
ones; a bare `nix fmt` reads stdin and formats nothing, and passing `.`
is deprecated.

```shell
nix fmt $(git ls-files '*.nix')
```

## 2. Evaluate and build the system closures — `nix flake check`

Why: catches evaluation errors, broken module options, and malformed
flake outputs, and proves each backend still produces a valid NixOS
system. Also exercises every test-suite module composed on top of the
imageless backend, the user account, and monitoring — the realistic
shape a consumer deploys.

What: evaluates every flake output and builds
`checks.<system>.{imageless,libvirt,controller,imageless-<suite>}`
— the three base closures and one closure per test-suite module. The
first run builds them all; later runs are cached. imageless-kvcache
carries nixos-unstable's torch and vLLM for the CPU, the longest of
them on a cold store. Add `--all-systems`
to also cover `aarch64-linux` (needs an aarch64 builder).

## 3. Build the templates

Why: step 2 builds the backends in isolation but not the templates the
downstream consumer actually copies in with `nix flake init`. The
templates have their own `flake.nix` with `inputs.nixos-flake.url`,
and a regression in their composition surfaces only on an actual build.
The libvirt template's qcow2 *disk image* is also assembled here —
`make-disk-image.nix` is not exercised by step 2's libvirt closure
check.

What: builds the imageless template's `toplevel` closure and the
libvirt template's qcow2 against the local checkout, in both cases
overriding the placeholder `path:/path/to/nixos-flake` input with the
working tree.

```shell
nix build "path:./templates/imageless#packages.x86_64-linux.toplevel" \
  --override-input nixos-flake "path:$PWD"

nix build "path:./templates/libvirt#packages.x86_64-linux.image" \
  --override-input nixos-flake "path:$PWD"
```

## 4. Build the custom packages — when `pkgs/` or `overlays/` changed

Why: `nix flake check` exercises the `checks` closures but does not build
the standalone `packages` outputs; overlay and package changes only
surface on an actual build.

What: builds every package the flake exports: the custom packages
from `pkgs/default.nix` and the tools it re-exports. The names come
from the flake rather than being repeated here, so a package added to
`pkgs/default.nix` is covered without editing this file, and one the
flake leaves out, such as an unfree package that evaluates only under a
system's allowUnfree, is not asked for (reading the attribute names
evaluates no package, so this stays fast):

```shell
nix build $(nix eval --raw .#packages.x86_64-linux --apply \
  'ps: builtins.concatStringsSep " " (map (n: ".#" + n) (builtins.attrNames ps))')
```

LMCache builds nixos-unstable's torch, which takes a while on a cold
store.

## 5. Review the commit message

Why: the project enforces strict commit conventions (see CLAUDE.md).

What: subject in imperative mood, at or below 50 characters; body in
plain-English paragraphs, never bullet lists; `Assisted-by:` ahead
of the closing `Signed-off-by:`, and every `Co-developed-by:`
immediately followed by that person's `Signed-off-by:`.
