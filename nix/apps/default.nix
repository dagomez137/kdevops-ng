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

  # Shared build sequence for the Windmill stack: the out-links the units read
  # through the %S state-dir specifier. Sets `state` and `sw` for callers.
  windmillBuild = ''
    state="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
    sw="$state/sw"
    nix build ./deploy/nix#windmill       --out-link "$sw/windmill"
    nix build ./deploy/nix#postgresql     --out-link "$sw/postgresql"
    nix build ./deploy/nix#db-setup       --out-link "$sw/db-setup"
    nix build ./deploy/nix#caddy          --out-link "$sw/caddy"
    nix build ./deploy/nix#windmill-extra --out-link "$sw/windmill-extra"
  '';

  # A plain menu printer (no repo cwd needed): `nix run` lists the commands.
  help = {
    type = "app";
    program = lib.getExe (writeShellApplication {
      name = "kdevops-help";
      text = ''
        cat <<'MENU'
        kdevops-ng development commands

          nix flake check                    verify: lint, formatting, generated drift
          nix develop .#checks -c bash scripts/check-style.sh
                                             whitespace, end-of-file, commit trailers
          nix fmt                            format the tree (nixfmt + ruff)
          nix run .#format                   ruff lint-fix and format Python
          nix run .#reflow                   rewrap wmill description fields
          nix develop .#checks -c pyright    type-check (advisory)
          nix run .#docs                     render docs to docs/_build/html
          nix run .#serve -- PORT            serve the HTML on 127.0.0.1 (default 8001)
          nix run .#maintainers -- FILE      who to Cc for a change
          nix develop .#checks               shell with all tooling on PATH
          nix run .#windmill-build           build the Windmill deploy stack
          nix run .#windmill-deploy          build, install, and enable it

        Details: docs/contributing/development.rst   Outputs: nix flake show
        MENU
      '';
    });
    meta.description = "List the project's development commands";
  };
in
{
  default = help;
  inherit help;
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
    description = "Render the docs, then serve them on 127.0.0.1 (arg: port)";
    runtimeInputs = [ toolsets.docsPython ];
    text = ''
      sphinx-build docs docs/_build/html
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

  # Build the Windmill stack (deploy/nix) to the GC-rooted out-links the
  # systemd --user units reach through the %S state-dir specifier. The server
  # build is heavy (~10 GB). nix is taken from the caller's environment.
  windmill-build = mkApp {
    name = "kdevops-windmill-build";
    description = "Build the Windmill deploy stack to its out-links";
    runtimeInputs = [ pkgs.coreutils ];
    text = windmillBuild;
  };

  # Build, install the units and Caddyfile, and enable the services: the
  # documented deploy sequence (docs/deployment/nix-backend.rst) as one command.
  # systemctl, loginctl, and nix come from the caller's running user session.
  windmill-deploy = mkApp {
    name = "kdevops-windmill-deploy";
    description = "Build, install, and enable the Windmill systemd --user stack";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      ${windmillBuild}
      config="''${XDG_CONFIG_HOME:-$HOME/.config}"
      mkdir --parents "$config/systemd/user" "$state"
      cp deploy/nix/systemd/*.service "$config/systemd/user/"
      cp deploy/nix/Caddyfile "$state/Caddyfile"
      loginctl enable-linger "$USER"
      systemctl --user daemon-reload
      systemctl --user enable --now \
        windmill-db windmill windmill-extra windmill-native windmill-caddy
      systemctl --user enable --now windmill-worker@0 windmill-worker@1
      echo "windmill deployed; reach the UI with: ssh -L 8000:localhost:8000 $USER@<host>"
    '';
  };
}
