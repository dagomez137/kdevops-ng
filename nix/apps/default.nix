# SPDX-License-Identifier: copyleft-next-0.3.1
#
# One app per Makefile verb, each a hermetic bundle of exactly its tools, so the
# Makefile is a thin forwarder (`make style` -> `nix run .#style`). The apps run
# from the repo root, where the scripts/ and f/ paths resolve. reflow and
# maintainers are not here yet; they land in a later phase.
{ pkgs }:
let
  toolsets = import ../toolsets.nix { inherit pkgs; };
  inherit (pkgs) lib writeShellApplication;
  app = pkg: {
    type = "app";
    program = lib.getExe pkg;
  };

  generated = writeShellApplication {
    name = "kdevops-generated";
    runtimeInputs = toolsets.gateRuntime;
    text = ''
      bash scripts/check-generated.sh
    '';
  };

  style = writeShellApplication {
    name = "kdevops-style";
    runtimeInputs = toolsets.gateRuntime;
    text = ''
      bash scripts/check-generated.sh
      ruff check scripts f
      ruff format --check scripts f
      bash scripts/check-style.sh
    '';
  };

  lint = writeShellApplication {
    name = "kdevops-lint";
    runtimeInputs = [ pkgs.ruff ];
    text = ''
      ruff check scripts f
      ruff format --check scripts f
    '';
  };

  format = writeShellApplication {
    name = "kdevops-format";
    runtimeInputs = [ pkgs.ruff ];
    text = ''
      ruff check --fix scripts f
      ruff format scripts f
    '';
  };

  typecheck = writeShellApplication {
    name = "kdevops-typecheck";
    runtimeInputs = [
      pkgs.pyright
      toolsets.pyEnv
    ];
    text = ''
      pyright
    '';
  };

  docs = writeShellApplication {
    name = "kdevops-docs";
    runtimeInputs = [ toolsets.docsPython ];
    text = ''
      sphinx-build docs docs/_build/html
      echo "docs ready: docs/_build/html/index.html"
    '';
  };

  serve = writeShellApplication {
    name = "kdevops-serve";
    runtimeInputs = [ toolsets.docsPython ];
    text = ''
      port="''${1:-8001}"
      python3 -m http.server "$port" --bind 127.0.0.1 --directory docs/_build/html
    '';
  };
in
{
  generated = app generated;
  style = app style;
  lint = app lint;
  format = app format;
  typecheck = app typecheck;
  docs = app docs;
  serve = app serve;
}
