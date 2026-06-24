# SPDX-License-Identifier: copyleft-next-0.3.1
#
# The nix backend for the Windmill instance (deploy/README.md). Builds a custom
# Windmill server from the dagomez137 fork and runs the whole stack from nix
# under `systemd --user`, replacing the podman backend. This flake exposes the
# windmill package (and the frontend FOD on its own for faster iteration); the
# systemd-user units land alongside it once the package builds.
{
  description = "Custom Windmill (dagomez137 fork) packaged for nix + systemd --user";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
      # oracledb pulls the unfree Oracle Instant Client; scope allowUnfree to
      # exactly that package rather than opening the whole package set.
      unfreePkgs =
        system:
        import nixpkgs {
          inherit system;
          config.allowUnfreePredicate =
            pkg: builtins.elem (nixpkgs.lib.getName pkg) [ "oracle-instantclient" ];
        };
    in
    {
      overlays.default = final: prev: {
        windmill = final.callPackage ./windmill/package.nix { };
      };

      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          windmill = pkgs.callPackage ./windmill/package.nix { };
        in
        {
          default = windmill;
          inherit windmill;
          # The frontend FOD on its own: lets `nix build .#windmill-frontend`
          # resolve npmDepsHash and iterate on UI changes without the Rust build.
          windmill-frontend = windmill.web-ui;
          # The full all_languages build including oracledb (unfree client).
          windmill-oracle = (unfreePkgs system).callPackage ./windmill/package.nix {
            withOracle = true;
          };
        }
      );

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);
    };
}
