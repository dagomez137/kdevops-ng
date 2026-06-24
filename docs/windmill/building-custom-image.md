# Building a custom Windmill image

The kdevops instance normally runs the upstream `ghcr.io/windmill-labs/windmill:main`
image (see `deploy/podman/`). When you need a server change that isn't in a release
yet (a frontend patch, a backend fix) you build a **custom image from the Windmill
source** and point the server container at it. This guide is the step-by-step.

The frontend is compiled **into** the server binary (Rust `rust_embed`, behind the
`static_frontend` Cargo feature), so a frontend change cannot be patched into the
running image by swapping files; the binary has to be rebuilt. That is the whole
reason this is a compile, not a copy.

## Prerequisites

- A Windmill source checkout at `~/src/windmill-labs/windmill`, on the branch that
  carries your patches (e.g. `integration/fixes` on top of `origin/main`).
- `podman` (rootless is fine; that is how kdevops runs everything).
- ~10 GB free disk and ~30-45 min for a cold build (full Rust compile).
- The build runs entirely from the repo's own multi-stage `Dockerfile`; nix is **not**
  used (a nix-built binary links against `/nix/store` and would not run in the debian
  runtime the image ships).

## The one thing to get right: `--build-arg features=...`

The Cargo features you compile with decide what the binary does. Two of them are
mandatory and easy to miss:

- **`static_frontend`**: embeds and serves the web UI. Without it the API still
  answers (`/api/version` returns 200) but **every page 404s → a white browser
  window**. It is **not** a default feature.
- **auth must stay on**: do not enable `no_auth`, or the instance serves with
  authentication disabled.

Use the **`oss_core,all_languages`** feature set. It is the fully-featured open-source
build with the UI and authentication, and it does **not** pull in enterprise code:

```
features = "oss_core,all_languages"
```

`oss_core` (`backend/Cargo.toml`) expands to the full OSS surface:
`static_frontend` (UI), `mcp`, `oauth2`, `http_trigger`, `websocket`, `mqtt_trigger`,
`postgres_trigger`, `native_trigger`, `smtp`, `embedding`, `parquet`, `quickjs`,
`bedrock`, `run_inline`, …; and `all_languages` adds the language workers.

### Do not use these

| Features | Why not |
|---|---|
| `""` (the Dockerfile default) | compiles `default = []` → no `static_frontend` → white screen |
| `ce` | what `:main` uses, but its chain pulls the **`private`** feature, which compiles `#[cfg(feature = "private")]` EE modules (`mod ee; mod email_ee; …`). Their `*_ee.rs` files are not in the OSS repo; they live in a sibling `../windmill-ee-private` and are copied in by `backend/substitute_ee_code.sh`. Without that repo the build fails with `error[E0583]: file not found for module ee`. The OSS repo carries non-gated `*_oss.rs` stubs that are used when `private` is off, so omitting it is the OSS path. |
| `oss` | it is `oss_core,all_languages` **plus `no_auth`**: disables authentication |
| `ee*` | enterprise builds |

(If you do have `windmill-ee-private` checked out beside the windmill repo, run
`backend/substitute_ee_code.sh` first and then `features=ce` builds, but a pure-OSS
box should use `oss_core,all_languages`.)

## 1. Build the image

```
cd ~/src/windmill-labs/windmill
git switch integration/fixes          # the branch with your patches
git log --oneline -3                  # confirm HEAD is what you expect

podman build \
    --build-arg features="oss_core,all_languages" \
    --tag windmill:integration-fixes \
    --file Dockerfile \
    .
```

The Dockerfile builds the frontend (`npm ci` + `npm run build`), compiles the backend
with the frontend embedded, and assembles the debian runtime. A background run is
convenient given the duration.

## 2. Verify the image serves the UI (not just the API)

This is the check that the `features=""` mistake skipped. After the build, point the
server at it (step 3) and confirm the **frontend**, not only the API:

```
curl --silent --output /dev/null --write-out '%{http_code}\n' http://localhost:8000/
curl --silent --output /dev/null --write-out '%{http_code}\n' http://localhost:8000/user/login
curl --silent http://localhost:8000/api/version
```

`/` and `/user/login` must be **200** (a UI-less binary returns 404 there while
`/api/version` still says 200; that asymmetry is the white-screen signature). For
extra confidence, fetch a hashed asset the page references:

```
asset=$(curl --silent http://localhost:8000/ | grep --only-matching --extended-regexp '/_app/[^"]+\.js' | head --lines 1)
curl --silent --output /dev/null --write-out "%{http_code}\n" "http://localhost:8000$asset"
```

## 3. Deploy it (rootless podman quadlet)

The UI server is the `windmill` container, defined by
`~/.config/containers/systemd/windmill.container`. Point its `Image=` at the new tag,
reload, and restart:

```
q=~/.config/containers/systemd/windmill.container
sed --in-place 's#^Image=.*#Image=localhost/windmill:integration-fixes#' "$q"
grep Image= "$q"

systemctl --user daemon-reload
systemctl --user restart windmill.service
```

Then re-run the step 2 checks against `http://localhost:8000/`.

Only the **server** needs the new image. The workers
(`windmill-worker*`, `windmill-native`) stay on `ghcr.io/windmill-labs/windmill:main`;
a frontend change is server-only and the worker protocol is unchanged. Reaching the UI
from a laptop is over the operator's SSH forward (e.g. mac `:8008` → remote
`localhost:8000`, which is what caddy publishes at `127.0.0.1:8000:80`).

## 4. Rollback

Keep a known-good image around (a prior build, or the upstream one) and the swap is the
same edit in reverse:

```
sed --in-place 's#^Image=.*#Image=ghcr.io/windmill-labs/windmill:main#' \
    ~/.config/containers/systemd/windmill.container
systemctl --user daemon-reload && systemctl --user restart windmill.service
```

A `windmill.container.bak.ghcr-main` backup of the quadlet is kept beside the live one.

## Notes

- Changing the `features` value invalidates the Dockerfile's cargo-chef dependency
  cache, so the next build recompiles dependencies from scratch; budget the full time.
- The image tag is arbitrary (`windmill:integration-fixes` here); just keep the quadlet
  `Image=` in sync with whatever you tag.
- Confirm the deployed binary is what you built: `curl http://localhost:8000/api/version`
  prints e.g. `CE v1.729.0-8-gbc2a036202`, where the trailing `-g<sha>` is the source
  commit.
