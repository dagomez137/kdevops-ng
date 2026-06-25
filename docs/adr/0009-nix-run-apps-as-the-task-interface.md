# `nix run` apps as the task and deploy interface

The flake models every developer and operator task as an `apps.<system>.<name>`
entry: the dev tasks (`format`, `reflow`, `docs`, `serve`, `maintainers`) and the
whole Windmill lifecycle (`windmill-build/install/activate/deploy`, the matching
teardown verbs, `windmill-trust/untrust`, and the `windmill-worker-*` pair). Every
one is schema-valid (`type = "app"`, a store-path `program` via
`writeShellApplication` and `lib.getExe`), so `nix flake check` is green. This
records whether `apps` is the right Nix abstraction for each, what we keep, and
the declarative form we defer.

## The honest framing

A Nix app is conceptually a portable program: `nix run github:owner/repo#name`
should work from anywhere. Ours do not. Each one changes into the checkout
(`cd "$(git rev-parse --show-toplevel)"`) and then acts on it with repo-relative
paths (`ruff scripts f`, `cp deploy/nix/...`, `nix build ./deploy/nix#...`). They
are workspace-bound task runners, not portable programs, and the `cd` is
deliberate. That is `apps` filling the `make`/`just` role, which this project
chose over a Makefile on purpose. It is a defensible idiom as long as the docs do
not imply portability; this ADR makes that explicit rather than leaving it as an
unstated wart.

## Decision

Keep the apps as the deliberate task and deploy interface for now, classified as
follows so each is a conscious choice and not drift:

- **Genuine programs** (`serve`, `windmill-untrust`, `maintainers`): real
  programs that take arguments and run something. `serve` is long-running;
  `untrust` runs the built `caddy`; `maintainers` wraps `get_maintainer.pl`.
  These are idiomatic apps, repo-coupling aside.
- **Really `nix build` of existing packages** (`windmill-build`): the components
  it builds (`windmill`, `postgresql`, `db-setup`, `caddy`, `windmill-extra`) are
  already `packages` in `deploy/nix`. The app is `nix build` of those plus the one
  thing plain `nix build` does not do: create the GC-rooted out-links under the
  state directory that the units reach through the `%S` specifier. That bundling
  is the reason it stays an app. Re-exporting the `deploy/nix` packages at the top
  level, or documenting `nix build ./deploy/nix#windmill`, is an optional cleanup,
  not a requirement.
- **Repo task runners** (`format`, `reflow`, the in-tree `docs` writer): tree
  mutators tied to the checkout. `format` is not `nix fmt` (that is format-only;
  `format` also runs `ruff check --fix`), so it earns its own verb. `docs` writes
  `docs/_build/html` for iterative preview; a reproducible `packages.docs` in the
  store is the idiomatic artifact form and an optional future split.
- **Imperative `systemd --user` orchestration** (`windmill-install`,
  `activate`, `deactivate`, `uninstall`, `wipe`, `teardown`, and the
  `windmill-worker-*` pair): these `cp` unit files, `systemctl --user enable/
  disable --now`, `loginctl enable-linger`, and `rm` state. They orchestrate the
  deploy backend's deliberately static, hand-editable units (see
  `docs/deployment/nix-backend.rst` and ADR-0008's sibling deploy decisions),
  built for the loopback, SSH-forward operator who tunes units with
  `systemctl --user edit`. This is where "apps as a `make` substitute" is most
  visible, and where an idiomatic declarative alternative genuinely exists.

## The deferred natural evolution

The idiomatic Nix way to manage `systemd --user` services is declarative: a
standalone home-manager `systemd.user.services.<name>` module, where activation is
`home-manager switch` and the units are generated, not hand-copied. This runs on
any Nix-equipped distro (Debian, Fedora, and the like); it does not require NixOS.
It is the natural next evolution of the Windmill lifecycle apps and is recorded
here as the planned direction, not adopted now.

It is deferred because it shifts the operator model the deploy backend chose:

- The operator adopts a `home.nix` and runs `home-manager switch` instead of
  `nix run .#windmill-deploy`, and the flake pulls in home-manager (a heavyweight
  input).
- home-manager owns and regenerates the base unit files, so they stop being
  hand-editable. Per-unit `systemctl --user edit` drop-ins survive (they live in a
  separate `<unit>.service.d/` path home-manager does not clobber), so overrides
  still work, but the static, directly-editable unit is gone.
- Linger stays manual: home-manager does not run `loginctl enable-linger` (a
  user-global setting), so that imperative step remains regardless.

Revisit when reproducible, fleet-consistent declarative deploy outweighs
hand-editable units (for example several worker hosts that must stay in lockstep),
and with the deploy session's agreement, since the static-unit choice is theirs.

## Status

accepted

## Consequences

- The current app set is kept as-is. No code moves in this change.
- The released docs carry the substance directly and do not link to this ADR
  (`.md` ADRs are development files, not part of the published rst). The
  development guide (`docs/contributing/development.rst`) states that the
  `nix run` commands are workspace-bound task runners, run inside the checkout,
  not portable programs. The deploy guide (`docs/deployment/nix-backend.rst`)
  states that the lifecycle is imperative over static units by design, and names
  the declarative home-manager module as the planned next evolution.
- The optional cleanups (a reproducible `packages.docs`, re-exporting the
  `deploy/nix` packages so `windmill-build` is a thin out-link convenience) are
  recorded as available, not scheduled.
- The standalone home-manager `systemd.user.services` migration of the Windmill
  lifecycle is the sequenced next evolution in the project TODO; it touches
  `deploy/nix`, so it does not proceed without the deploy session.
