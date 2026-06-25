# SPDX-License-Identifier: copyleft-next-0.3.1
#
# The project's developer and CI entry point. kdevops-ng does its tooling in
# nix: this root flake owns the repo's own tooling (lint, format, type-check,
# docs) as devShells and apps, so `make` targets are thin forwarders to
# `nix run .#<verb>`.
#
# Organisation is by concern, not by tool: devShells are per workflow (checks,
# docs), apps are per verb. The worker-runtime build shells stay in
# vendor/nixos-flake because workers reach them by path; only developer-facing
# tooling lives here. That library becomes an input in the phase that re-exports
# its shells, not before.
{
  description = "kdevops-ng developer and CI tooling for the Windmill workspace";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { nixpkgs, treefmt-nix, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pkgsFor = system: nixpkgs.legacyPackages.${system};
      treefmtFor = system: treefmt-nix.lib.evalModule (pkgsFor system) ./nix/treefmt.nix;
      # One toolsets evaluation per system, shared by the devShells and the apps
      # so a shell and its matching app can never drift.
      perSystem = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          toolsets = import ./nix/toolsets.nix { inherit pkgs; };
        in
        {
          devShells = import ./nix/devshells { inherit pkgs toolsets; };
          apps = import ./nix/apps { inherit pkgs toolsets; };
        }
      );
    in
    {
      devShells = nixpkgs.lib.mapAttrs (_: v: v.devShells) perSystem;
      apps = nixpkgs.lib.mapAttrs (_: v: v.apps) perSystem;
      formatter = forAllSystems (system: (treefmtFor system).config.build.wrapper);
    };
}
