# deploy — pick a backend

Three interchangeable ways to run the same Windmill instance (localhost:8000,
same DB, same `wmill.yaml`). Bring up one, then `wmill sync push`.

| Backend | Dir | Runtime | Status |
|---|---|---|---|
| Podman | `podman/` | rootless containers + Quadlet (systemd --user) | ✅ working |
| Distro | `distro/` | release binary + apt postgres, systemd services (no container runtime) | TODO |
| Nix    | `nix/`    | flake / NixOS module | TODO |
