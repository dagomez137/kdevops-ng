# SPDX-License-Identifier: copyleft-next-0.3.1
{
  pkgs,
  toolsets,
  wmill,
}:
let
  checks = import ./checks.nix { inherit pkgs toolsets; };
  docs = import ./docs.nix { inherit pkgs toolsets; };
  default = pkgs.mkShell {
    inputsFrom = [ checks ];
    packages = [ wmill ];
  };
in
{
  inherit checks docs default;
}
