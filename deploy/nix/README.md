# deploy/nix: the nix backend

This is the nix backend slot from [`../README.md`](../README.md): it builds a
custom Windmill server from source with nix and (in progress) runs the whole
stack under `systemd --user`, replacing the podman image. A nix-built binary
links `/nix/store` and runs natively on the host, which is exactly what we want
here: there is no debian runtime to be incompatible with.

The server is built from the downstream fork
`github.com/dagomez137/windmill` (branch `integration/fixes`), which carries
frontend patches not yet upstream. The frontend is compiled into the Rust
binary through the `static_frontend` Cargo feature, so a frontend change is a
full rebuild, never a file swap.

## Prerequisites

Nix with the unified CLI (the `nix-command flakes` experimental features). The
build is heavy: roughly 10 GB and a clean compile of about 18 minutes on a
fast machine, plus a large frontend fixed-output derivation (the npm deps) and
the cargo vendor.

## Build

```
cd deploy/nix
nix build .#windmill            # the server, all 13 free languages
```

The result symlink's `bin/windmill` is a wrapper that sets the interpreter
paths for every language (`PYTHON_PATH`, `DENO_PATH`, `JAVA_PATH`, `RUBY_PATH`,
`RSCRIPT_PATH`, `NU_PATH`, and the rest) to their `/nix/store` locations.

Other outputs:

```
nix build .#windmill-frontend   # just the embedded SvelteKit UI (a FOD)
nix build .#windmill-oracle      # all 14 languages, adds oracledb
```

`.#windmill-frontend` is the frontend on its own. It builds without the long
Rust compile, so it is the fast way to iterate on a UI change and to
re-resolve the frontend hash after a fork bump.

`.#windmill-oracle` adds the `oracledb` language. Its `oracle` crate links the
unfree Oracle Instant Client, so this variant pulls an unfree dependency
(`allowUnfree` is scoped to just that client in `flake.nix`). The default
`.#windmill` deliberately leaves it out so the common build stays free.

## What is in the build

The feature set is `oss_core` plus the explicit language list, which is the
fully featured open-source surface with authentication on. It is not the
enterprise build, and not the `no_auth` variant. Concretely the languages are
Python, Deno/TypeScript, Bun, Go, PHP, C#, PowerShell, Bash, Rust, Ruby, Java
(`R`), nushell, plus MySQL, MS SQL (kerberos), BigQuery, and DuckDB; with
`.#windmill-oracle`, Oracle as well.

## Verify it serves the UI (not just the API)

A server with the API but no embedded UI answers `/api/version` with 200 while
every page 404s (the white-screen signature). The smoke test below stands up a
throwaway postgres, runs the server against it, and checks the pages and a
hashed asset:

```
WM=$(nix build .#windmill --no-link --print-out-paths)
PGDATA=$(mktemp --directory)/pg
nix shell nixpkgs#postgresql_16 --command initdb -D "$PGDATA" -U postgres --auth=trust
nix shell nixpkgs#postgresql_16 --command pg_ctl -D "$PGDATA" \
    -o "-p 5433 -h 127.0.0.1 -k /tmp" --wait start
nix shell nixpkgs#postgresql_16 --command createdb -h 127.0.0.1 -p 5433 -U postgres windmill

DATABASE_URL='postgres://postgres@127.0.0.1:5433/windmill?sslmode=disable' \
    MODE=server PORT=8009 "$WM/bin/windmill" &

curl --silent --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:8009/
curl --silent --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:8009/user/login
curl --silent http://127.0.0.1:8009/api/version
```

`/` and `/user/login` must both return 200, and `/api/version` reports the
fork version (`CE v1.738.0`). Stop the server and run
`pg_ctl -D "$PGDATA" stop` when done.

## Running the full stack

`install.sh` builds every component to a GC-rooted out-link under
`$XDG_STATE_HOME/windmill-nix/sw`, renders the `systemd --user` units into the
user unit directory, and brings the instance up. Idempotent: re-run it to pick
up a new build or a changed unit.

```
./deploy/nix/install.sh
```

That starts postgres (`127.0.0.1:5432`, in a cluster under the user state dir,
with the role password rotated off the shared default on first boot), the
server (`MODE=server`, internal `127.0.0.1:8002`), the windmill-extra LSP
gateway (`127.0.0.1:3001`), one native worker, the build pool, and caddy. Caddy
is the only public boundary: it binds loopback and fronts the server and the
LSP gateway on one origin, so the UI is reached over an SSH forward.

```
ssh -L 8000:localhost:8000 <user>@<host>   # then http://localhost:8000
```

Knobs (environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `CADDY_PORT` | `8000` | loopback port caddy fronts the stack on |
| `WORKERS` | `2` | build-pool worker count |
| `VM_WORKERS` / `VM_RUN_WORKERS` | `0` | vm and vm-run pools (need the workbench; see below) |
| `WORKBENCH_DIR` / `SYSTEM_DIR` / `WORKERS_DIR` / `VENDOR_DIR` | under the repo | build-area paths |

The vm and vm-run pools default off: they drive QEMU/systemd VMs and need the
workbench provisioned (the System bare, the peer ssh key, the `vhost_vsock`
module), which is a separate step. `teardown.sh` stops and removes the units;
`teardown.sh --purge` also wipes the cluster and secrets.

Two operational notes:

- The units reuse the same names as the podman backend
  (`windmill.service`, `windmill-db.service`, …), and static user units shadow
  podman's quadlet-generated ones, so the two backends cannot run at once.
  Deploy one. To switch from podman, retire its quadlets first
  (`~/.config/containers/systemd/windmill*`).
- `systemctl --user` from a shell without a login session needs
  `XDG_RUNTIME_DIR=/run/user/$(id -u)` and a matching
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus`.

## Bumping the fork

The derivation pins the fork revision and four content hashes. To move to a
newer fork commit, edit `windmill/package.nix`:

1. Set `src.rev` to the new commit and zero its `hash` (use a fake
   `sha256-AAAA...`); a build prints the real one. Or prefetch it directly:
   `nix-prefetch-url --unpack https://github.com/dagomez137/windmill/archive/<rev>.tar.gz`
   and convert with `nix hash to-sri --type sha256 <base32>`.
2. If the `v8` crate version changed (check `backend/Cargo.lock` for the
   `name = "v8"` entry), bump `windmill/librusty_v8.nix` to match and refetch
   its two `shas` from the denoland rusty_v8 release.
3. Re-resolve the frontend hash: zero `web-ui.npmDepsHash`, run
   `nix build .#windmill-frontend`, copy the reported hash.
4. Re-resolve the crate hash: zero `cargoHash`, run
   `nix build .#windmill.cargoDeps`, copy the reported hash.

If a build fails inside `postPatch`, the `substituteInPlace` anchors in
`windmill/package.nix` or the mount edits in `fix-nsjail.awk` drifted with the
source and need updating; both are written to be resilient to line moves but
not to a rename of the function or file they target.

## How the derivation departs from nixpkgs

`windmill/package.nix` is modeled on the nixpkgs `windmill` package (1.601.1)
but vendored and retargeted to the fork (1.738.0). The header comment in that
file and the inline notes explain each adaptation the version gap required:
the `mold` linker, the `postPatch` transforms that replace stale line patches
(with `fix-nsjail.awk` for the sandbox mounts), `bindgenHook` for
`libgssapi-sys`, disabling `cargo-auditable`, and turning off incremental
compilation so the final link stays under the argument-length limit.
