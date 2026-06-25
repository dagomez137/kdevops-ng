# SPDX-License-Identifier: copyleft-next-0.3.1
#
# The project's developer and CI entry point. kdevops-ng does its tooling in
# nix, and each output uses the mechanism that fits its purpose: read-only
# verification is `checks` (run by `nix flake check`), `devShells` carry the
# tools for interactive and advisory use, `apps` are the programs that mutate,
# serve, build, or query, and `formatter` is treefmt for `nix fmt`. The Makefile
# is a thin front door that routes each target to the right one of these.
#
# The worker-runtime build shells stay in vendor/nixos-flake because workers
# reach them by path; only developer-facing tooling lives here. That library
# becomes an input in the phase that re-exports its shells, not before.
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
    {
      self,
      nixpkgs,
      treefmt-nix,
      ...
    }:
    let
      # Only the systems this tooling is actually built and run on. The deploy
      # backend targets more; this developer/CI flake does not, so listing more
      # would only add phantom outputs and `nix flake check` omission warnings.
      systems = [ "x86_64-linux" ];
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
          # Verification: lint and drift checks, plus treefmt's own formatting
          # check so `nix flake check` is the whole CI gate.
          checks = import ./nix/checks.nix { inherit pkgs self toolsets; } // {
            formatting = (treefmtFor system).config.build.check self;
          };
        }
      );
    in
    {
      devShells = nixpkgs.lib.mapAttrs (_: v: v.devShells) perSystem;
      apps = nixpkgs.lib.mapAttrs (_: v: v.apps) perSystem;
      checks = nixpkgs.lib.mapAttrs (_: v: v.checks) perSystem;
      formatter = forAllSystems (system: (treefmtFor system).config.build.wrapper);
    };
}
