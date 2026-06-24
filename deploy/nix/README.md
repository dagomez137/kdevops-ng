# deploy/nix: the nix backend

The nix backend builds a custom Windmill server from source with nix and runs
the whole stack (server, postgres, workers, the LSP gateway, caddy) under
`systemd --user`, with no container runtime. It is the default backend.

The contents:

- `flake.nix` — the packages (`windmill`, `windmill-oracle`, `windmill-frontend`,
  `postgresql`, `db-setup`, `caddy`, `windmill-extra`).
- `windmill/` — the Windmill derivation, built from the `dagomez137/windmill`
  fork; see its `package.nix` header for the build details.
- `windmill-extra/` — the LSP gateway derivation.
- `bin/windmill-db-setup` — cluster init, password rotation, database creation.
- `systemd/*.service` — static `systemd --user` units.
- `Caddyfile` — the reverse proxy config (internal TLS by default).

There is no install script. The build, deploy, and configuration steps, and the
TLS and base-URL pairing, are documented in the Sphinx site under
**Deployment → Nix backend** (`docs/nix-backend.rst`).
