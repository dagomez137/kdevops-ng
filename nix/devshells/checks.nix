# SPDX-License-Identifier: copyleft-next-0.3.1
#
# The gate toolset in one interactive shell: everything `make style`/`make lint`
# and the repo's own nix linting reach for. `nix develop .#checks`.
{ pkgs, toolsets }:
pkgs.mkShell {
  packages = toolsets.checkTools ++ [
    pkgs.bash
    pkgs.git
  ];
}
