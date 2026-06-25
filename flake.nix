# SPDX-License-Identifier: copyleft-next-0.3.1
#
# The project's developer and CI entry point. kdevops-ng uses nix for everything:
# this root flake composes the vendored library and the deploy backend and owns
# the repo's own tooling (lint, format, type-check, docs) as devShells and apps,
# so `make` targets are thin forwarders to `nix run .#<verb>`.
#
# Organisation is by concern, not by tool: devShells are per workflow (checks,
# docs), apps are per verb (style, lint, format, typecheck, docs, serve). The
# worker-runtime build shells stay in vendor/nixos-flake because workers reach
# them by path; only developer-facing tooling lives here.
{
  description = "kdevops-ng developer and CI tooling for the Windmill workspace";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

    # The vendored library flake (tracked by git-subrepo). Declared so its
    # nixpkgs is pinned once here via follows, and as the seam for re-exporting
    # its worker-runtime devShells later. Its outputs are not consumed yet.
    nixos-flake = {
      url = "path:./vendor/nixos-flake";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pkgsFor = system: nixpkgs.legacyPackages.${system};
    in
    {
      devShells = forAllSystems (system: import ./nix/devshells { pkgs = pkgsFor system; });
      apps = forAllSystems (system: import ./nix/apps { pkgs = pkgsFor system; });
      formatter = forAllSystems (system: (pkgsFor system).nixfmt);
    };
}
