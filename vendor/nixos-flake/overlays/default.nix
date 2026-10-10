# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Package overlays and custom packages. Each file in this directory
# modifies one nixpkgs package. Custom packages not in nixpkgs are
# defined in pkgs/ using callPackage and merged here.
#
# The packages in pkgs/ that come from nixos-unstable need the flake's
# pkgsUnstable, so use the flake's overlays.default rather than importing
# this file:
#   { nixpkgs.overlays = [ nixos-flake.overlays.default ]; }
#
# Reference: https://nixos.org/manual/nixpkgs/stable/#chap-overlays
final: prev:
prev.lib.composeManyExtensions [
  (import ./fio.nix)
  (import ./xfstests.nix)
  (import ./xfsprogs.nix)
  (import ./spdk.nix)
  (overlayFinal: _: import ../pkgs overlayFinal)
] final prev
