# deploy: pick a deployment method

Ways to run the Windmill instance (localhost:8000, one DB, one `wmill.yaml`).
Deploy one, then `wmill sync push`.

| Method | Dir | Runtime | Status |
|---|---|---|---|
| Nix    | `nix/`    | nix flake + systemd --user (no container runtime) | ✅ default |
| Distro | `distro/` | release binary + apt postgres, systemd services (no container runtime) | planned |

To run a patched server (a fix not yet in a release), the `nix` method builds a
custom Windmill from the pinned fork; see [nix/](nix/).
