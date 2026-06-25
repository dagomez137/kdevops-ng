# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Per-workflow developer shells, one per task rather than one per tool. checks
# carries the gate toolset; docs the Sphinx toolchain. default is the gate shell
# until a richer dev shell lands. toolsets is shared with the apps (see flake.nix).
{ pkgs, toolsets }:
let
  checks = import ./checks.nix { inherit pkgs toolsets; };
  docs = import ./docs.nix { inherit pkgs toolsets; };
in
{
  inherit checks docs;
  default = checks;
}
