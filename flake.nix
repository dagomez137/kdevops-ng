# SPDX-License-Identifier: copyleft-next-0.3.1
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
      inherit (nixpkgs) lib;
      systems = [ "x86_64-linux" ];
      forAllSystems = lib.genAttrs systems;
      pkgsFor = system: nixpkgs.legacyPackages.${system};
      treefmtFor = system: treefmt-nix.lib.evalModule (pkgsFor system) ./nix/treefmt.nix;
      lintSrc = lib.fileset.toSource {
        root = ./.;
        fileset = lib.fileset.unions [
          ./scripts
          ./f
          ./pyproject.toml
        ];
      };
      generatedSrc = lib.fileset.toSource {
        root = ./.;
        fileset = lib.fileset.unions [
          ./scripts
          ./f
        ];
      };
      perSystem = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          toolsets = import ./nix/toolsets.nix { inherit pkgs; };
        in
        {
          devShells = import ./nix/devshells { inherit pkgs toolsets; };
          apps = import ./nix/apps { inherit pkgs toolsets; };
          checks =
            import ./nix/checks.nix {
              inherit
                pkgs
                lintSrc
                generatedSrc
                toolsets
                ;
            }
            // {
              formatting = (treefmtFor system).config.build.check self;
            };
        }
      );
    in
    {
      devShells = lib.mapAttrs (_: v: v.devShells) perSystem;
      apps = lib.mapAttrs (_: v: v.apps) perSystem;
      checks = lib.mapAttrs (_: v: v.checks) perSystem;
      formatter = forAllSystems (system: (treefmtFor system).config.build.wrapper);
    };
}
