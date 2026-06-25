# SPDX-License-Identifier: copyleft-next-0.3.1
#
# One app per Makefile verb, each a hermetic bundle of exactly its tools, so the
# Makefile is a thin forwarder (`make style` -> `nix run .#style`). Every app
# changes into the repo root first, so it works regardless of the caller's cwd.
{ pkgs, toolsets }:
let
  inherit (pkgs) lib writeShellApplication;

  # Build a `nix run` app from a tool bundle and a script body. The body runs
  # from the repo root; meta.description shows in `nix flake show` and clears the
  # `nix flake check` missing-meta warning.
  mkApp =
    {
      name,
      description,
      runtimeInputs,
      text,
    }:
    let
      program = writeShellApplication {
        inherit name;
        runtimeInputs = [ pkgs.git ] ++ runtimeInputs;
        text = ''
          root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
            echo "kdevops: run this inside the kdevops-ng checkout" >&2
            exit 1
          }
          cd "$root"
          ${text}
        '';
      };
    in
    {
      type = "app";
      program = lib.getExe program;
      meta = { inherit description; };
    };
in
{
  generated = mkApp {
    name = "kdevops-generated";
    description = "Check that committed generated files match their generators";
    runtimeInputs = toolsets.gateRuntime;
    text = "bash scripts/check-generated.sh";
  };

  style = mkApp {
    name = "kdevops-style";
    description = "Run the full pre-commit gate (generated, ruff, whitespace)";
    runtimeInputs = toolsets.gateRuntime;
    text = ''
      bash scripts/check-generated.sh
      ruff check scripts f
      ruff format --check scripts f
      bash scripts/check-style.sh
    '';
  };

  lint = mkApp {
    name = "kdevops-lint";
    description = "Lint and format-check all Python with ruff";
    runtimeInputs = [ pkgs.ruff ];
    text = ''
      ruff check scripts f
      ruff format --check scripts f
    '';
  };

  format = mkApp {
    name = "kdevops-format";
    description = "Apply ruff lint fixes and formatting to all Python";
    runtimeInputs = [ pkgs.ruff ];
    text = ''
      ruff check --fix scripts f
      ruff format scripts f
    '';
  };

  typecheck = mkApp {
    name = "kdevops-typecheck";
    description = "Type-check Python with pyright (advisory)";
    runtimeInputs = [
      pkgs.pyright
      toolsets.pyEnv
    ];
    text = "pyright";
  };

  docs = mkApp {
    name = "kdevops-docs";
    description = "Render the documentation to docs/_build/html with Sphinx";
    runtimeInputs = [ toolsets.docsPython ];
    text = ''
      sphinx-build docs docs/_build/html
      echo "docs ready: docs/_build/html/index.html"
    '';
  };

  serve = mkApp {
    name = "kdevops-serve";
    description = "Serve the built HTML on 127.0.0.1 (arg: port, default 8001)";
    runtimeInputs = [ toolsets.docsPython ];
    text = ''
      port="''${1:-8001}"
      python3 -m http.server "$port" --bind 127.0.0.1 --directory docs/_build/html
    '';
  };

  reflow = mkApp {
    name = "kdevops-reflow";
    description = "Rewrap wmill description fields into clean literal blocks";
    runtimeInputs = [ toolsets.pyEnv ];
    text = "python3 scripts/reflow-descriptions.py --write";
  };

  maintainers = mkApp {
    name = "kdevops-maintainers";
    description = "Who to Cc for a change (args: one or more files)";
    runtimeInputs = [ pkgs.perl ];
    text = ''perl scripts/get_maintainer.pl --no-tree --no-git-fallback -f "$@"'';
  };
}
