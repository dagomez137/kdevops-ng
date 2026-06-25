# SPDX-License-Identifier: copyleft-next-0.3.1
{ pkgs, toolsets }:
let
  checks = import ./checks.nix { inherit pkgs toolsets; };
  docs = import ./docs.nix { inherit pkgs toolsets; };
in
{
  inherit checks docs;
  default = checks;
}
