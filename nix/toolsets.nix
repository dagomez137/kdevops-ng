# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Shared tool bundles consumed by both the devShells and the apps, so a tool is
# declared once and a shell and its matching `nix run` app never drift.
{ pkgs }:
let
  # Python with the gate scripts' only third-party dependency (gen-bringup and
  # reflow-descriptions parse the wmill yaml with PyYAML).
  pyEnv = pkgs.python3.withPackages (ps: [ ps.pyyaml ]);

  # The Sphinx documentation toolchain, relocated from vendor/nixos-flake. The
  # PyData theme is pinned ahead of the channel via its published wheel.
  docsPython = pkgs.python3.withPackages (ps: [
    ps.sphinx
    ps.sphinx-copybutton
    (ps.pydata-sphinx-theme.overridePythonAttrs (_: rec {
      version = "0.19.0";
      src = ps.fetchPypi {
        pname = "pydata_sphinx_theme";
        inherit version;
        format = "wheel";
        dist = "py3";
        python = "py3";
        hash = "sha256-XX3+O+sPrMiLXXj/SkyUjyFMwOA6rifn/FgobpY7WIs=";
      };
    }))
  ]);
in
{
  inherit pyEnv docsPython;

  # Everything the gate scripts (check-style.sh, check-generated.sh) shell out
  # to, so the style and generated apps are hermetic.
  gateRuntime = [
    pkgs.bash
    pkgs.coreutils
    pkgs.gnugrep
    pkgs.git
    pkgs.ruff
    pyEnv
  ];

  # Interactive lint, format, and type tools, plus the repo's own nix linters,
  # for `nix develop .#checks`.
  checkTools = [
    pkgs.ruff
    pkgs.pyright
    pyEnv
    pkgs.nixfmt
    pkgs.statix
    pkgs.deadnix
    pkgs.shellcheck
  ];
}
