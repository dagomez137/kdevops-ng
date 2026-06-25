# SPDX-License-Identifier: copyleft-next-0.3.1
#
# The Sphinx documentation toolchain, relocated from vendor/nixos-flake: it
# renders kdevops-ng's own docs, a downstream concern that does not belong in the
# library flake. `nix develop .#docs --command sphinx-build docs docs/_build/html`.
{ pkgs, toolsets }:
pkgs.mkShell {
  packages = [ toolsets.docsPython ];
}
