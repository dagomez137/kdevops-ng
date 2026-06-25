# deploy: pick a deployment method

Three interchangeable ways to run the same Windmill instance (localhost:8000,
same DB, same `wmill.yaml`). Deploy one, then `wmill sync push`.

| Method | Dir | Runtime | Status |
|---|---|---|---|
| Podman | `podman/` | rootless containers + Quadlet (systemd --user) | ✅ working |
| Distro | `distro/` | release binary + apt postgres, systemd services (no container runtime) | TODO |
| Nix    | `nix/`    | flake / NixOS module | TODO |

To run a patched server (a fix not yet in a release) instead of the upstream
`windmill:main` image, see
[docs/windmill/building-custom-image.md](../docs/windmill/building-custom-image.md).
