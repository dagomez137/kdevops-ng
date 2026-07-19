# SPDX-License-Identifier: copyleft-next-0.3.1
{ pkgs }:
let
  # PyYAML: gen-bringup and reflow-descriptions parse the wmill yaml.
  # pytest: the tests/ fixture suite over the f/ step modules.
  # Jinja2 and cryptography: imported at module level by the f/qsu render
  # modules and f/workbench/ssh_key the fixture tests import.
  pyEnv = pkgs.python3.withPackages (ps: [
    ps.pyyaml
    ps.pytest
    ps.jinja2
    ps.cryptography
  ]);

  # PyData theme pinned ahead of the channel via its published wheel.
  docsPython = pkgs.python3.withPackages (ps: [
    ps.sphinx
    ps.sphinx-autobuild
    ps.sphinx-copybutton
    ps.sphinx-design
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

  # What check-style.sh and check-generated.sh shell out to.
  gateRuntime = [
    pkgs.bash
    pkgs.coreutils
    pkgs.gnugrep
    pkgs.git
    pkgs.ruff
    pyEnv
  ];

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
