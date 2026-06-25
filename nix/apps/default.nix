# SPDX-License-Identifier: copyleft-next-0.3.1
#
# `nix run` apps are programs the flake provides: each verb here either mutates
# the tree (format, reflow), serves it (serve), builds it (docs), or queries it
# (maintainers). Read-only verification lives in nix/checks.nix (run by `nix
# flake check`), not here; advisory pyright and the git-aware style script run
# from the checks devShell. Every app changes into the repo root first, so it
# works regardless of the caller's cwd.
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
  format = mkApp {
    name = "kdevops-format";
    description = "Apply ruff lint fixes and formatting to all Python";
    runtimeInputs = [ pkgs.ruff ];
    text = ''
      ruff check --fix scripts f
      ruff format scripts f
    '';
  };

  reflow = mkApp {
    name = "kdevops-reflow";
    description = "Rewrap wmill description fields into clean literal blocks";
    runtimeInputs = [ toolsets.pyEnv ];
    text = "python3 scripts/reflow-descriptions.py --write";
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

  maintainers = mkApp {
    name = "kdevops-maintainers";
    description = "Who to Cc for a change (args: one or more files)";
    runtimeInputs = [ pkgs.perl ];
    text = ''perl scripts/get_maintainer.pl --no-tree --no-git-fallback -f "$@"'';
  };
}
