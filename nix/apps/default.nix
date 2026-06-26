# SPDX-License-Identifier: copyleft-next-0.3.1
{ pkgs, toolsets }:
let
  inherit (pkgs) lib writeShellApplication;

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

  windmillBuild = ''
    state="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
    pkgs="$state/pkgs"
    nix build .#windmill       --out-link "$pkgs/windmill"
    nix build .#postgresql     --out-link "$pkgs/postgresql"
    nix build .#db-setup       --out-link "$pkgs/db-setup"
    nix build .#caddy          --out-link "$pkgs/caddy"
    nix build .#windmill-extra --out-link "$pkgs/windmill-extra"
  '';

  windmillInstall = ''
    state="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
    config="''${XDG_CONFIG_HOME:-$HOME/.config}"
    mkdir --parents "$config/systemd/user" "$config/windmill"
    cp deploy/nix/systemd/*.service "$config/systemd/user/"
    cp deploy/nix/Caddyfile "$config/windmill/Caddyfile"
    # The vendor tree the workers resolve via VENDOR_DIR (the nixos-flake's
    # `#git` and build devShells, the qemu-system-units templates). Copy it in,
    # so it is decoupled from the checkout location and a separate host has it.
    rm --recursive --force "$state/vendor"
    mkdir --parents "$state/vendor"
    cp --recursive --no-preserve=mode vendor/. "$state/vendor/"
    # Default worker mix: @0000-0001 build, @0002 vm, @0003 vm-run.
    mkdir --parents \
      "$config/systemd/user/windmill-worker@0002.service.d" \
      "$config/systemd/user/windmill-worker@0003.service.d"
    cp --no-preserve=mode ${workerVmDropIn} \
      "$config/systemd/user/windmill-worker@0002.service.d/group.conf"
    cp --no-preserve=mode ${workerVmRunDropIn} \
      "$config/systemd/user/windmill-worker@0003.service.d/group.conf"
  '';

  windmillActivate = ''
    systemctl --user daemon-reload
    loginctl enable-linger "$USER"
    systemctl --user enable --now \
      windmill-db windmill windmill-extra windmill-native windmill-caddy
    systemctl --user enable --now windmill-worker@0000 windmill-worker@0001 \
      windmill-worker@0002 windmill-worker@0003
  '';

  # Linger stays: it is user-global; the disable-linger app drops it.
  windmillDeactivate = ''
    config="''${XDG_CONFIG_HOME:-$HOME/.config}"
    # systemctl stop takes a glob; disable does not. Stop by glob, then disable
    # each unit that has an install symlink, the worker instances included.
    systemctl --user stop 'windmill*' || true
    shopt -s nullglob
    for link in "$config"/systemd/user/default.target.wants/windmill*; do
      systemctl --user disable "''${link##*/}"
    done
    systemctl --user daemon-reload
  '';

  windmillUninstall = ''
    state="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
    config="''${XDG_CONFIG_HOME:-$HOME/.config}"
    rm --force "$config/systemd/user/"windmill*.service
    rm --recursive --force "$config/systemd/user/"windmill-worker@*.service.d
    rm --force "$config/windmill/Caddyfile"
    rm --recursive --force "$state/vendor"
    systemctl --user daemon-reload
  '';

  # Spares the build-area workbench under the same state dir.
  windmillWipe = ''
    state="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
    rm --recursive --force "$state/pgdata" "$state/pkgs" "$state/env"
  '';

  windmillTrust = ''
    root="''${XDG_DATA_HOME:-$HOME/.local/share}/caddy/pki/authorities/local/root.crt"
    if [ ! -f "$root" ]; then
      echo "kdevops: no caddy root CA at $root" >&2
      echo "kdevops: activate the stack first (nix run .#windmill-activate)" >&2
      exit 1
    fi
    echo "caddy root CA: $root"
    cat <<EOF
    Trust it where the browser runs. For a local browser, trust the file above
    on this host. For a remote browser, copy it to the SSH-forward client first:
      scp "$USER@<host>:$root" windmill-root.crt
    then trust it there:
      Firefox  Settings > Privacy & Security > Certificates > Authorities > Import
      NSS      certutil -d sql:~/.pki/nssdb -A -t "C,," -n windmill-local -i windmill-root.crt
      macOS    security add-trusted-cert -d -r trustRoot -k login.keychain windmill-root.crt
    EOF
  '';

  windmillUntrust = ''
    root="''${XDG_DATA_HOME:-$HOME/.local/share}/caddy/pki/authorities/local/root.crt"
    caddy="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill/pkgs/caddy/bin/caddy"
    if [ ! -x "$caddy" ]; then
      echo "kdevops: caddy not built at $caddy (nix run .#windmill-build)" >&2
      exit 1
    fi
    "$caddy" untrust --cert "$root"
  '';

  # Empty Requires=/After=/EnvironmentFile= reset the list: no local db here.
  workerRemoteDropIn = pkgs.writeText "windmill-worker-remote.conf" ''
    [Unit]
    Requires=
    After=

    [Service]
    EnvironmentFile=
    EnvironmentFile=-%E/windmill/windmill-worker.env
  '';

  # Per-instance group/tags for the vm workers the default deploy ships.
  workerVmDropIn = pkgs.writeText "windmill-worker-vm.conf" ''
    [Service]
    Environment=WORKER_GROUP=vm
    Environment=WORKER_TAGS=vm
  '';
  workerVmRunDropIn = pkgs.writeText "windmill-worker-vm-run.conf" ''
    [Service]
    Environment=WORKER_GROUP=vm
    Environment=WORKER_TAGS=vm-run
  '';

  help = {
    type = "app";
    program = lib.getExe (writeShellApplication {
      name = "kdevops-help";
      text = ''
        cat <<'MENU'
        kdevops-ng development commands

          nix flake check   lint, format, generated drift
          nix fmt           format the tree
          nix flake show    list the runnable apps (nix run .#<name>)

        Full guide and the pre-commit gate: docs/contributing/development.rst
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
      sphinx-build --write-all docs docs/_build/html
      echo "docs ready: docs/_build/html/index.html"
    '';
  };

  serve = mkApp {
    name = "kdevops-serve";
    description = "Live-render the docs on 127.0.0.1, rebuilding on save (arg: port)";
    runtimeInputs = [ toolsets.docsPython ];
    text = ''
      port="''${1:-8001}"
      sphinx-autobuild --write-all docs docs/_build/html --host 127.0.0.1 --port "$port"
    '';
  };

  maintainers = mkApp {
    name = "kdevops-maintainers";
    description = "Who to Cc for a change (args: one or more files)";
    runtimeInputs = [ pkgs.perl ];
    text = ''perl scripts/get_maintainer.pl --no-tree --no-git-fallback -f "$@"'';
  };

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

  windmill-trust = {
    type = "app";
    program = lib.getExe (writeShellApplication {
      name = "kdevops-windmill-trust";
      runtimeInputs = [ pkgs.coreutils ];
      text = windmillTrust;
    });
    meta.description = "Show the caddy root CA path to trust on the SSH-forward client";
  };

  windmill-untrust = {
    type = "app";
    program = lib.getExe (writeShellApplication {
      name = "kdevops-windmill-untrust";
      text = windmillUntrust;
    });
    meta.description = "Untrust the caddy root CA from this host's trust store";
  };

  windmill-worker-install = mkApp {
    name = "kdevops-windmill-worker-install";
    description = "Build and install the Windmill worker unit for a remote server";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      state="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
      config="''${XDG_CONFIG_HOME:-$HOME/.config}"
      units="$config/systemd/user"
      nix build .#windmill --out-link "$state/pkgs/windmill"
      mkdir --parents "$units/windmill-worker@.service.d"
      cp deploy/nix/systemd/windmill-worker@.service "$units/"
      cp --no-preserve=mode ${workerRemoteDropIn} \
        "$units/windmill-worker@.service.d/remote-server.conf"
      systemctl --user daemon-reload
      echo "worker installed. Point it at the server's database, then enable:"
      echo "  systemctl --user edit windmill-worker@"
      echo "    [Service]"
      echo "    Environment=DATABASE_URL=postgres://USER:PW@SERVER:5432/windmill"
      echo "  nix run .#windmill-worker-activate -- N"
    '';
  };

  windmill-worker-activate = mkApp {
    name = "kdevops-windmill-worker-activate";
    description = "Enable and start N Windmill worker instances; re-run to scale";
    text = ''
      count="''${1:-1}"
      case "$count" in
      "" | *[!0-9]*)
        echo "usage: nix run .#windmill-worker-activate -- <count>" >&2
        exit 1
        ;;
      esac
      loginctl enable-linger "$USER"
      for ((i = 0; i < count; i++)); do
        printf -v idx '%04d' "$i"
        systemctl --user enable --now "windmill-worker@$idx"
      done
      last=$(printf '%04d' "$((count - 1))")
      echo "enabled worker@0000..$last. Scale up by re-running this with a"
      echo "larger count; a single instance can also be added with:"
      echo "  systemctl --user enable --now windmill-worker@<NNNN>"
    '';
  };

  disable-linger = {
    type = "app";
    program = lib.getExe (writeShellApplication {
      name = "kdevops-disable-linger";
      text = ''loginctl disable-linger "$USER"'';
    });
    meta.description = "Disable user linger (user-global: stops all lingering services)";
  };
}
