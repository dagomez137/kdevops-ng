# SPDX-License-Identifier: copyleft-next-0.3.1
{
  pkgs,
  toolsets,
  wmill,
}:
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
            echo "kdevops-ng: run this inside the kdevops-ng checkout" >&2
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

  # The user unit dir rides the operator's home into guests over the home
  # share, where a guest's user manager would start these host units. Each
  # installed unit gets a drop-in pinning it to this host's machine id
  # (ConditionHost), so any other machine's manager skips it. Runs on the
  # host being installed, so /etc/machine-id is that host's.
  hostOnlyDropIns = glob: ''
    machine_id="$(cat /etc/machine-id)"
    shopt -s nullglob
    for unit in "$config"/systemd/user/${glob}; do
      mkdir --parents "$unit.d"
      printf '[Unit]\nConditionHost=%s\n' "$machine_id" > "$unit.d/host-only.conf"
    done
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
    ${hostOnlyDropIns "windmill*.service"}
  '';

  # The VM discovery sweep reaches a peer workbench host with
  # `ssh -F SYSTEM_DIR/ssh/config` (f/qsu/common), never through ~/.ssh, so
  # each alias is resolved from the operator's own config here, once, into a
  # Host block that uses the managed peer key. The registry the sweep reads,
  # SYSTEM_DIR/peers, stays f/workbench/fetch's to write.
  windmillPeers = ''
    state="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
    system="''${SYSTEM_DIR:-$state/workbench/system}"
    ssh_dir="$system/ssh"
    key="$ssh_dir/peer_ed25519"
    conf="$ssh_dir/config.d/peers.conf"
    if [ $# -eq 0 ]; then
      echo "usage: nix run .#windmill-peers -- <alias>..." >&2
      exit 2
    fi
    mkdir --parents "$ssh_dir/config.d"
    [ -f "$key" ] || ssh-keygen -t ed25519 -N "" -C kdevops-workbench-peer -f "$key"
    # The init flow's ssh_key step writes the config with this Include; seed it
    # so a peer resolves before the first init.
    grep --quiet --no-messages 'config.d/\*.conf' "$ssh_dir/config" \
      || printf 'Include %s/config.d/*.conf\n' "$ssh_dir" > "$ssh_dir/config"
    # Built beside the file and moved over it, so a refused alias leaves the
    # previous blocks in place.
    trap 'rm --force "$conf.tmp"' EXIT
    : > "$conf.tmp"
    seen=" "
    for peer in "$@"; do
      case "$peer" in
      "" | -* | *[[:space:]]*) echo "kdevops-ng: not an ssh alias: '$peer'" >&2; exit 1 ;;
      esac
      case "$seen" in *" $peer "*) continue ;; esac
      seen="$seen$peer "
      resolved="$(ssh -G -- "$peer")" || { echo "kdevops-ng: ssh cannot resolve $peer" >&2; exit 1; }
      host="" port="" user="" proxy=""
      while read -r option value; do
        case "$option" in
        hostname) host="$value" ;;
        port) port="$value" ;;
        user) user="$value" ;;
        proxyjump | proxycommand) [ "$value" = none ] || proxy="$option $value" ;;
        esac
      done <<< "$resolved"
      if [ -z "$host" ] || [ -z "$port" ] || [ -z "$user" ]; then
        echo "kdevops-ng: ssh -G $peer gave no hostname, port or user" >&2
        exit 1
      fi
      # Only HostName, Port and User carry over.
      if [ -n "$proxy" ]; then
        echo "kdevops-ng: $peer needs '$proxy', which the block cannot carry; use an alias the workers reach directly" >&2
        exit 1
      fi
      cat >> "$conf.tmp" <<EOF
    Host $peer
        HostName $host
        Port $port
        User $user
        IdentityFile $key
        IdentitiesOnly yes
        StrictHostKeyChecking accept-new
        UserKnownHostsFile $ssh_dir/known_hosts
    EOF
      if [ "$port" = 22 ]; then name="$host"; else name="[$host]:$port"; fi
      ssh-keygen -F "$name" -f "$ssh_dir/known_hosts" > /dev/null 2>&1 \
        || ssh-keyscan -p "$port" "$host" 2> /dev/null >> "$ssh_dir/known_hosts" \
        || echo "kdevops-ng: no host key from $host:$port; the first ssh accepts it" >&2
      echo "peer $peer -> $user@$host:$port"
    done
    # OpenSSH refuses a group-writable config, included files too.
    chmod go-w "$ssh_dir/config" "$conf.tmp"
    mv "$conf.tmp" "$conf"
    echo "wrote $conf; authorize $key.pub on each peer, then register the peers"
    echo "through the f/workbench init flow (its peers input)"
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
    rm --recursive --force "$config/systemd/user/"windmill*.service.d
    rm --force "$config/windmill/Caddyfile"
    rm --recursive --force "$state/vendor"
    systemctl --user daemon-reload
  '';

  # Spares the build-area workbench under the same state dir.
  windmillWipe = ''
    state="''${XDG_STATE_HOME:-$HOME/.local/state}/windmill"
    rm --recursive --force "$state/pgdata" "$state/pkgs" "$state/env"
  '';

  # The optional monitoring stack (Grafana + Prometheus + Loki) beside the
  # Windmill deploy: own out-links and units under the monitoring name, its
  # Grafana database inside the Windmill postgres cluster. Optional means the
  # windmill-* apps never touch it and deactivating it leaves Windmill up.
  monitoringBuild = ''
    state="''${XDG_STATE_HOME:-$HOME/.local/state}/monitoring"
    pkgs="$state/pkgs"
    nix build .#grafana             --out-link "$pkgs/grafana"
    nix build .#prometheus          --out-link "$pkgs/prometheus"
    nix build .#loki                --out-link "$pkgs/loki"
    nix build .#monitoring-db-setup --out-link "$pkgs/monitoring-db-setup"
  '';

  monitoringInstall = ''
    config="''${XDG_CONFIG_HOME:-$HOME/.config}"
    mkdir --parents "$config/systemd/user" "$config/monitoring"
    cp deploy/nix/systemd/monitoring-*.service "$config/systemd/user/"
    cp deploy/nix/monitoring/prometheus.yml "$config/monitoring/prometheus.yml"
    cp deploy/nix/monitoring/loki.yaml "$config/monitoring/loki.yaml"
    rm --recursive --force "$config/monitoring/grafana"
    mkdir --parents "$config/monitoring/grafana"
    cp --recursive --no-preserve=mode deploy/nix/monitoring/grafana/provisioning \
      "$config/monitoring/grafana/provisioning"
    cp --recursive --no-preserve=mode deploy/nix/monitoring/dashboards \
      "$config/monitoring/grafana/dashboards"
    ${hostOnlyDropIns "monitoring-*.service"}
  '';

  monitoringActivate = ''
    systemctl --user daemon-reload
    loginctl enable-linger "$USER"
    systemctl --user enable --now \
      monitoring-prometheus monitoring-loki monitoring-grafana
  '';

  monitoringDeactivate = ''
    config="''${XDG_CONFIG_HOME:-$HOME/.config}"
    systemctl --user stop 'monitoring-*' || true
    shopt -s nullglob
    for link in "$config"/systemd/user/default.target.wants/monitoring-*; do
      systemctl --user disable "''${link##*/}"
    done
    systemctl --user daemon-reload
  '';

  windmillTrust = ''
    root="''${XDG_DATA_HOME:-$HOME/.local/share}/caddy/pki/authorities/local/root.crt"
    if [ ! -f "$root" ]; then
      echo "kdevops-ng: no caddy root CA at $root" >&2
      echo "kdevops-ng: activate the stack first (nix run .#windmill-activate)" >&2
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
      echo "kdevops-ng: caddy not built at $caddy (nix run .#windmill-build)" >&2
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
      name = "kdevops-ng-help";
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
    name = "kdevops-ng-format";
    description = "Apply ruff lint fixes and formatting to all Python";
    runtimeInputs = [ pkgs.ruff ];
    text = ''
      ruff check --fix scripts f tests
      ruff format scripts f tests
    '';
  };

  reflow = mkApp {
    name = "kdevops-ng-reflow";
    description = "Rewrap wmill description fields into clean literal blocks";
    runtimeInputs = [ toolsets.pyEnv ];
    text = "python3 scripts/reflow-descriptions.py --write";
  };

  preview-smoke = mkApp {
    name = "kdevops-ng-preview-smoke";
    description = "Smoke the suite flows' isolated steps as Windmill preview jobs";
    runtimeInputs = [
      toolsets.pyEnv
      wmill
    ];
    text = ''python3 scripts/preview-smoke.py "$@"'';
  };

  deploy-kdevops = mkApp {
    name = "kdevops-ng-deploy-kdevops";
    description = "Push the workspace to kdevops, then smoke the deployed suite steps";
    runtimeInputs = [
      toolsets.pyEnv
      wmill
    ];
    text = ''
      workspace=kdevops
      wmill sync push --workspace "$workspace" "$@"
      case " $* " in
      *" --dry-run "* | *" --help "* | *" -h "*) ;;
      *) python3 scripts/preview-smoke.py --remote --workspace "$workspace" ;;
      esac
    '';
  };

  docs = mkApp {
    name = "kdevops-ng-docs";
    description = "Render the documentation to docs/_build/html with Sphinx";
    runtimeInputs = [ toolsets.docsPython ];
    text = ''
      sphinx-build --write-all docs docs/_build/html
      echo "docs ready: docs/_build/html/index.html"
    '';
  };

  serve = mkApp {
    name = "kdevops-ng-serve";
    description = "Live-render the docs on 127.0.0.1, rebuilding on save (arg: port)";
    runtimeInputs = [ toolsets.docsPython ];
    text = ''
      port="''${1:-8001}"
      sphinx-autobuild --write-all docs docs/_build/html --host 127.0.0.1 --port "$port"
    '';
  };

  maintainers = mkApp {
    name = "kdevops-ng-maintainers";
    description = "Who to Cc for a change (args: one or more files)";
    runtimeInputs = [ pkgs.perl ];
    text = ''perl scripts/get_maintainer.pl --no-tree --no-git-fallback -f "$@"'';
  };

  windmill-build = mkApp {
    name = "kdevops-ng-windmill-build";
    description = "Build the Windmill deploy stack to its out-links";
    text = windmillBuild;
  };

  windmill-install = mkApp {
    name = "kdevops-ng-windmill-install";
    description = "Install the Windmill systemd --user units and Caddyfile";
    runtimeInputs = [ pkgs.coreutils ];
    text = windmillInstall;
  };

  windmill-peers = mkApp {
    name = "kdevops-ng-windmill-peers";
    description = "Write the workers' ssh config for the peer workbench hosts (args: aliases)";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.gnugrep
      pkgs.openssh
    ];
    text = windmillPeers;
  };

  windmill-activate = mkApp {
    name = "kdevops-ng-windmill-activate";
    description = "Enable and start the Windmill systemd --user services";
    text = windmillActivate;
  };

  windmill-deploy = mkApp {
    name = "kdevops-ng-windmill-deploy";
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
    name = "kdevops-ng-windmill-deactivate";
    description = "Stop and disable the Windmill systemd --user services";
    text = windmillDeactivate;
  };

  windmill-uninstall = mkApp {
    name = "kdevops-ng-windmill-uninstall";
    description = "Remove the installed Windmill units and Caddyfile";
    runtimeInputs = [ pkgs.coreutils ];
    text = windmillUninstall;
  };

  windmill-wipe = mkApp {
    name = "kdevops-ng-windmill-wipe";
    description = "Delete the Windmill instance data (database, out-links, env)";
    runtimeInputs = [ pkgs.coreutils ];
    text = windmillWipe;
  };

  windmill-teardown = mkApp {
    name = "kdevops-ng-windmill-teardown";
    description = "Deactivate, uninstall, and wipe the whole Windmill stack";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      ${windmillDeactivate}
      ${windmillUninstall}
      ${windmillWipe}
      echo "windmill torn down and wiped"
    '';
  };

  monitoring-build = mkApp {
    name = "kdevops-ng-monitoring-build";
    description = "Build the monitoring stack (Grafana, Prometheus, Loki) to its out-links";
    text = monitoringBuild;
  };

  monitoring-install = mkApp {
    name = "kdevops-ng-monitoring-install";
    description = "Install the monitoring systemd --user units and configs";
    runtimeInputs = [ pkgs.coreutils ];
    text = monitoringInstall;
  };

  monitoring-activate = mkApp {
    name = "kdevops-ng-monitoring-activate";
    description = "Enable and start the monitoring systemd --user services";
    text = monitoringActivate;
  };

  monitoring-deploy = mkApp {
    name = "kdevops-ng-monitoring-deploy";
    description = "Build, install, and activate the whole monitoring stack";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      ${monitoringBuild}
      ${monitoringInstall}
      ${monitoringActivate}
      echo "monitoring deployed; reach Grafana with: ssh -L 3000:localhost:3000 $USER@<host>"
    '';
  };

  monitoring-deactivate = mkApp {
    name = "kdevops-ng-monitoring-deactivate";
    description = "Stop and disable the monitoring systemd --user services";
    text = monitoringDeactivate;
  };

  monitoring-collector-install = mkApp {
    name = "kdevops-ng-monitoring-collector-install";
    description = "Install the guest telemetry fan-in for a worker host";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      state="''${XDG_STATE_HOME:-$HOME/.local/state}/monitoring"
      config="''${XDG_CONFIG_HOME:-$HOME/.config}"
      nix build .#alloy --out-link "$state/pkgs/alloy"
      mkdir --parents "$config/systemd/user" "$config/monitoring"
      cp deploy/nix/systemd/monitoring-collector.service \
        deploy/nix/systemd/monitoring-tunnel.service "$config/systemd/user/"
      cp deploy/nix/monitoring/collector.alloy "$config/monitoring/collector.alloy"
      ${hostOnlyDropIns "monitoring-*.service"}
      systemctl --user daemon-reload
      echo "collector installed. Point it at the primary, then enable:"
      echo "  $config/monitoring/monitoring-tunnel.env:"
      echo "    MONITORING_PRIMARY=<user@primary-host>"
      echo "  $config/monitoring/monitoring-collector.env:"
      echo "    PRIMARY_METRICS_URL=http://127.0.0.1:19090/api/v1/write"
      echo "    PRIMARY_LOGS_URL=http://127.0.0.1:13100/loki/api/v1/push"
      echo "  systemctl --user enable --now monitoring-tunnel monitoring-collector"
    '';
  };

  windmill-trust = {
    type = "app";
    program = lib.getExe (writeShellApplication {
      name = "kdevops-ng-windmill-trust";
      runtimeInputs = [ pkgs.coreutils ];
      text = windmillTrust;
    });
    meta.description = "Show the caddy root CA path to trust on the SSH-forward client";
  };

  windmill-untrust = {
    type = "app";
    program = lib.getExe (writeShellApplication {
      name = "kdevops-ng-windmill-untrust";
      text = windmillUntrust;
    });
    meta.description = "Untrust the caddy root CA from this host's trust store";
  };

  windmill-worker-install = mkApp {
    name = "kdevops-ng-windmill-worker-install";
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
      ${hostOnlyDropIns "windmill-worker@.service"}
      systemctl --user daemon-reload
      echo "worker installed. Point it at the server's database, then enable:"
      echo "  systemctl --user edit windmill-worker@"
      echo "    [Service]"
      echo "    Environment=DATABASE_URL=postgres://USER:PW@SERVER:5432/windmill"
      echo "  nix run .#windmill-worker-activate -- N"
    '';
  };

  windmill-worker-activate = mkApp {
    name = "kdevops-ng-windmill-worker-activate";
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
      name = "kdevops-ng-disable-linger";
      text = ''loginctl disable-linger "$USER"'';
    });
    meta.description = "Disable user linger (user-global: stops all lingering services)";
  };
}
