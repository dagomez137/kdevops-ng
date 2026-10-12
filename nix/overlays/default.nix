# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Package overlays for the repository tooling. Each file modifies one nixpkgs
# package, as in vendor/nixos-flake/overlays.
final: prev:
prev.lib.composeManyExtensions [
  (import ./b4.nix)
] final prev
