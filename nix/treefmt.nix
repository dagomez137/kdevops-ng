# SPDX-License-Identifier: copyleft-next-0.3.1
#
# treefmt configuration driving `nix fmt`: one pass formats the whole tree,
# nixfmt for Nix and ruff for Python (line length and rules from pyproject.toml).
# The vendored library and the deploy backend own their own formatting and are
# excluded so `nix fmt` never reformats files this project does not own.
{
  projectRootFile = "flake.nix";
  programs.nixfmt.enable = true;
  programs.ruff-format.enable = true;
  settings.global.excludes = [
    "vendor/**"
    "deploy/**"
  ];
}
