# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Per-workflow developer shells, one per task rather than one per tool. checks
# carries the gate toolset; docs the Sphinx toolchain. default is the gate shell
# until a richer dev shell lands.
{ pkgs }:
let
  toolsets = import ../toolsets.nix { inherit pkgs; };
  checks = import ./checks.nix { inherit pkgs toolsets; };
  docs = import ./docs.nix { inherit pkgs toolsets; };
in
{
  inherit checks docs;
  default = checks;
}
