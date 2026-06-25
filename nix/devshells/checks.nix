# SPDX-License-Identifier: copyleft-next-0.3.1
{ pkgs, toolsets }:
pkgs.mkShell {
  packages = toolsets.checkTools ++ [
    pkgs.bash
    pkgs.git
  ];
}
