# CLAUDE.md

## Project Overview

Library flake of NixOS modules, overlays, and templates for
provisioning NixOS systems, built around two backend modules that
produce different artifacts: `backends.imageless` and
`backends.libvirt`. [README.md](README.md) describes the modes they
serve and how each boots.

**License**: copyleft-next-0.3.1

## Project Structure

Directories, not files: the tree itself is the file list, and a copy
of it here only falls behind.

```
nixos-flake/
├── flake.nix          nixosModules, overlays, templates, packages, checks, devShells
├── flake.lock         Pinned nixpkgs revision
├── lib/               Package sets shared between a profile and a devShell
├── docs/              usage, design-decisions, verifying
├── modules/
│   ├── backends/      One directory per backend: the module plus its user.nix defaults
│   ├── profiles/      Opt-in system roles, each gated or composed on a backend
│   ├── mounts/        Opt-in virtiofs shares and block-device mounts
│   ├── testSuites/    One module per test suite
│   ├── user.nix       Opt-in unprivileged account
│   └── user-options.nix  Shared option schema for that account
├── pkgs/              Packages not in nixpkgs, via callPackage
├── overlays/          One file per overridden nixpkgs package, plus pkgs/
├── templates/         Starter configurations for `nix flake init --template`
├── LICENSES/
├── COPYING            License overview and dual-licensing guidance
├── LICENSE            Copyright and license reference
└── README.md
```

## Critical Rules

### Never fabricate facts

Every NixOS option must be verified against the NixOS options search
(search.nixos.org/options) or the nixpkgs source.

### Long-form command options

NEVER use short flags when a long-form alternative exists.
`--template` not `-t`, `--recursive` not `-r`, `--parents` not
`-p`. Exception: tools without long-form options (`ssh -p`).

### Reference only upstream projects

Comments and commit messages describe this flake and the upstream
projects it packages — the Linux kernel, QEMU, xfstests, SPDK, BCC,
and so on. Never name the downstream projects that combine this
flake, nor the pipeline that drives it. The flake stands on its own;
how a consumer wires it up belongs in that consumer's tree.

## Rules

The decisions are written where they take effect: each module states
in its own comments why it sets an option the way it does, and
[docs/design-decisions.md](docs/design-decisions.md) carries the
upstream mechanism behind them, with the nixpkgs and systemd sources.
Read the module first; it is the only copy that cannot drift from the
code.

```
modules/backends/imageless/default.nix  tmpfs root, virtiofs store,
                                        external kernel, initramfs
modules/backends/libvirt/default.nix    grub on vda, ext4 root,
                                        nixpkgs kernel, scripted DHCP
modules/backends/*/user.nix             per-backend account defaults
modules/user-options.nix                why the schema is shared
modules/profiles/controller.nix         the control-node role
overlays/default.nix                    how the overlays compose
templates/*/default.nix                 what a starter imports
```

[docs/usage.md](docs/usage.md) is the consumer-facing half: how to
compose the modules, override a package from a local checkout, and
declare shares and storage.

## Build

Before committing, run the verification steps in
[docs/verifying.md](docs/verifying.md). It is the single checklist:
format, flake check, template builds, package builds, and a commit
message review.

## Git Commit Guidelines

### One commit per change

Atomic commits. Spell fixes go in separate commits from code changes.

### Commit message format

```
subsystem: brief description in imperative mood (max 50 chars)

Plain English explanation of the change, 1-3 short paragraphs.
NEVER use bullet points or itemized lists in commit messages.

Assisted-by: LLM
Signed-off-by: Your Name <your.email@example.org>
```

The subject line stays at or below 50 characters.

### Use Signed-off-by and Assisted-by tags

Assisted-by MUST be immediately followed by Signed-off-by with NO
empty lines between them. No Co-Authored-By trailer.

Assisted-by is the Linux kernel's tag for AI-assisted work, defined in
Documentation/process/coding-assistants.rst by commit 78d979db6cef
("docs: add AI Coding Assistants documentation", v7.0-rc1) and made a
submission requirement by 6252e5c1c20e ("docs: add an Assisted-by
mention to submitting-patches.rst"). The full form is
`Assisted-by: LLM [TOOL1] [TOOL2]`, where the optional names are
specialized analysis tools actually run (coccinelle, sparse, smatch,
clang-tidy) and never basic ones such as git, gcc, make or an editor,
so the bare tag is the usual form. Commits predating this carry
Generated-by: Claude AI; leave them alone.

### No shopping cart lists

NEVER use bullet points or itemized lists in commit messages. Use
plain English paragraphs.

### Subsystem prefix

Prefix with the part of the flake the change touches: `flake:` for
flake.nix, `modules:` or the specific module name for a module,
`overlays:` for package overlays, `pkgs:` for custom packages,
`templates:` for template changes, `docs:` for documentation in
docs/, `README:` for README changes, `CLAUDE:` for this file. Use
`tree:` for the rare change that genuinely spans the whole
repository.

## Related work

See the Related work table in [README.md](README.md).
