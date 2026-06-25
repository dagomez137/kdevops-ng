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
      runtimeInputs ? [ ],
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

  # The Windmill deploy stages, each a self-contained shell snippet so its app
  # runs on its own and windmill-deploy can compose all three.
  #
  # build: compile the components to the out-links the units read through the %S
  # state-dir specifier.
  windmillBuild = ''
    state="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
    sw="$state/sw"
    nix build ./deploy/nix#windmill       --out-link "$sw/windmill"
    nix build ./deploy/nix#postgresql     --out-link "$sw/postgresql"
    nix build ./deploy/nix#db-setup       --out-link "$sw/db-setup"
    nix build ./deploy/nix#caddy          --out-link "$sw/caddy"
    nix build ./deploy/nix#windmill-extra --out-link "$sw/windmill-extra"
  '';

  # install: place the units and Caddyfile where the user manager and proxy read
  # them (%E is $XDG_CONFIG_HOME, %S is $XDG_STATE_HOME).
  windmillInstall = ''
    state="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
    config="''${XDG_CONFIG_HOME:-$HOME/.config}"
    mkdir --parents "$config/systemd/user" "$state"
    cp deploy/nix/systemd/*.service "$config/systemd/user/"
    cp deploy/nix/Caddyfile "$state/Caddyfile"
  '';

  # activate: reload the manager onto the installed units, linger the user so
  # they run without a login session, then enable (persist) and start the
  # services and workers. `enable --now` enables and starts in one step.
  windmillActivate = ''
    systemctl --user daemon-reload
    loginctl enable-linger "$USER"
    systemctl --user enable --now \
      windmill-db windmill windmill-extra windmill-native windmill-caddy
    systemctl --user enable --now windmill-worker@0000 windmill-worker@0001
  '';

  # The teardown stages mirror deploy in reverse.
  #
  # deactivate: stop and disable the services and any worker instances (`disable
  # --now` disables the [Install] symlinks and stops in one step). The glob also
  # catches worker instances beyond @0 and @1. Linger is NOT dropped here: it is
  # user-global, so disabling it would stop every other lingering user service,
  # the workbench mirrors included. The separate disable-linger app does that.
  windmillDeactivate = ''
    systemctl --user disable --now 'windmill*'
  '';

  # uninstall: remove the installed units and the Caddyfile, then reload.
  windmillUninstall = ''
    config="''${XDG_CONFIG_HOME:-$HOME/.config}"
    state="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
    rm --force "$config/systemd/user/"windmill*.service
    rm --force "$state/Caddyfile"
    systemctl --user daemon-reload
  '';

  # wipe: delete the instance data (the database cluster, the build out-links,
  # and the generated env) under the state dir. Destructive; the build-area
  # workbench under the same state dir is left alone. Run after deactivate so the
  # cluster is stopped.
  windmillWipe = ''
    state="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
    rm --recursive --force "$state/pgdata" "$state/sw" "$state/env"
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
          nix run .#windmill-install         install its systemd units + Caddyfile
          nix run .#windmill-activate        enable and start its services
          nix run .#windmill-deploy          build, install, and activate at once
          nix run .#windmill-deactivate      stop and disable its services
          nix run .#windmill-uninstall       remove its units + Caddyfile
          nix run .#windmill-wipe            delete its data (database, out-links)
          nix run .#windmill-teardown        deactivate, uninstall, and wipe at once
          nix run .#disable-linger           drop user linger (user-global; opt-in)

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

  # The Windmill deploy stack (deploy/nix), built and run under systemd --user.
  # nix, systemctl, and loginctl come from the caller's running user session;
  # the server build is heavy (~10 GB). The three stages run on their own for
  # step-by-step control, and windmill-deploy runs all of them at once.
  windmill-build = mkApp {
    name = "kdevops-windmill-build";
    description = "Build the Windmill deploy stack to its out-links";
    text = windmillBuild;
  };

  windmill-install = mkApp {
    name = "kdevops-windmill-install";
    description = "Install the Windmill systemd --user units and Caddyfile";
    runtimeInputs = [ pkgs.coreutils ];
    text = windmillInstall;
  };

  windmill-activate = mkApp {
    name = "kdevops-windmill-activate";
    description = "Enable and start the Windmill systemd --user services";
    text = windmillActivate;
  };

  windmill-deploy = mkApp {
    name = "kdevops-windmill-deploy";
    description = "Build, install, and activate the whole Windmill stack";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      ${windmillBuild}
      ${windmillInstall}
      ${windmillActivate}
      echo "windmill deployed; reach the UI with: ssh -L 8000:localhost:8000 $USER@<host>"
    '';
  };

  windmill-deactivate = mkApp {
    name = "kdevops-windmill-deactivate";
    description = "Stop and disable the Windmill systemd --user services";
    text = windmillDeactivate;
  };

  windmill-uninstall = mkApp {
    name = "kdevops-windmill-uninstall";
    description = "Remove the installed Windmill units and Caddyfile";
    runtimeInputs = [ pkgs.coreutils ];
    text = windmillUninstall;
  };

  windmill-wipe = mkApp {
    name = "kdevops-windmill-wipe";
    description = "Delete the Windmill instance data (database, out-links, env)";
    runtimeInputs = [ pkgs.coreutils ];
    text = windmillWipe;
  };

  windmill-teardown = mkApp {
    name = "kdevops-windmill-teardown";
    description = "Deactivate, uninstall, and wipe the whole Windmill stack";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      ${windmillDeactivate}
      ${windmillUninstall}
      ${windmillWipe}
      echo "windmill torn down and wiped"
    '';
  };

  # Not a Windmill operation: linger is per-user and user-global. Windmill
  # teardown deliberately leaves it on because other lingering services (the
  # workbench mirrors) depend on it; this opt-in target turns it off explicitly.
  # No repo cwd, so it is a plain app rather than going through mkApp.
  disable-linger = {
    type = "app";
    program = lib.getExe (writeShellApplication {
      name = "kdevops-disable-linger";
      text = ''loginctl disable-linger "$USER"'';
    });
    meta.description = "Disable user linger (user-global: stops all lingering services)";
  };
}
