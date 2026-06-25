# SPDX-License-Identifier: copyleft-next-0.3.1
{
  projectRootFile = "flake.nix";
  programs.nixfmt.enable = true;
  programs.ruff-format.enable = true;
  settings.global.excludes = [
    "vendor/**"
    "deploy/**"
  ];
}
